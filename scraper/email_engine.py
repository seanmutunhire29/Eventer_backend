import base64
import email
import imaplib
import json
import re
from datetime import timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.conf import settings
from django.utils import timezone

from core.models import Event, ScrapeSource
from scraper.engine import upsert_event
from scraper.models import ProcessedEmail

# Anthropic-supported image media types.
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MIN_IMAGE_BYTES = 3_000  # skip tiny logos/tracking pixels
MAX_IMAGE_BYTES = 4_500_000  # stay well under the API's per-image limit
MAX_IMAGES_PER_EMAIL = 4


class EmailAuthError(Exception):
    pass


def _get_body_text(msg):
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and plain is None:
                plain = part.get_payload(decode=True)
            elif content_type == "text/html" and html is None:
                html = part.get_payload(decode=True)
    else:
        if msg.get_content_type() == "text/html":
            html = msg.get_payload(decode=True)
        else:
            plain = msg.get_payload(decode=True)

    charset = "utf-8"
    if plain:
        return plain.decode(charset, errors="ignore")
    if html:
        soup = BeautifulSoup(html.decode(charset, errors="ignore"), "lxml")
        return soup.get_text(separator="\n", strip=True)
    return ""


def _get_images(msg):
    """Pulls inline images and image attachments (event flyers are often
    sent as a picture with no real body text) as base64, ready for the
    Anthropic vision API. Skips tiny images (icons/tracking pixels) and
    caps count/size to keep requests reasonable."""
    images = []
    if not msg.is_multipart():
        return images

    for part in msg.walk():
        if len(images) >= MAX_IMAGES_PER_EMAIL:
            break
        content_type = part.get_content_type()
        if content_type not in SUPPORTED_IMAGE_TYPES:
            continue
        payload = part.get_payload(decode=True)
        if not payload or not (MIN_IMAGE_BYTES <= len(payload) <= MAX_IMAGE_BYTES):
            continue
        images.append(
            {
                "media_type": content_type,
                "data": base64.standard_b64encode(payload).decode("ascii"),
            }
        )
    return images


def _connect(source):
    if not settings.IMAP_EMAIL or not settings.IMAP_APP_PASSWORD:
        raise EmailAuthError(
            "IMAP_EMAIL / IMAP_APP_PASSWORD are not set in .env"
        )
    imap = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
    try:
        imap.login(settings.IMAP_EMAIL, settings.IMAP_APP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        raise EmailAuthError(
            "IMAP login failed. If this is a Microsoft 365 / Office365 "
            "account (most .edu addresses), the tenant has very likely "
            "disabled legacy basic auth for IMAP — app passwords won't "
            "work there and you'll need OAuth2 (Microsoft Graph API / "
            f"XOAUTH2) instead. Original error: {exc}"
        ) from exc
    folder = (source.selector_config or {}).get("folder", "INBOX")
    status, response = imap.select(folder, readonly=True)
    if status != "OK":
        imap.logout()
        raise EmailAuthError(
            f"Could not open folder/label {folder!r} ({response!r}). "
            "Check that it exists in the mailbox and the name matches "
            "exactly (case-sensitive, and Gmail nested labels use "
            "'Parent/Child')."
        )
    return imap


def fetch_candidate_emails(source):
    """Returns a list of dicts for inbox messages that are new (not in
    ProcessedEmail). No keyword pre-filtering — every new message within the
    lookback window goes to the LLM, which decides for itself whether it
    contains a real event. Never marks messages as read (uses BODY.PEEK)."""
    config = source.selector_config or {}
    lookback_days = int(config.get("lookback_days", 14))

    imap = _connect(source)
    try:
        since = (timezone.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        status, data = imap.search(None, f'(SINCE "{since}")')
        if status != "OK":
            return []
        uids = data[0].split()
        if not uids:
            return []

        already_processed = set(
            ProcessedEmail.objects.filter(scrape_source=source).values_list(
                "message_id", flat=True
            )
        )

        candidates = []
        for uid in uids:
            status, header_data = imap.fetch(
                uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT DATE)])"
            )
            if status != "OK" or not header_data or not header_data[0]:
                continue
            header_msg = email.message_from_bytes(header_data[0][1])
            message_id = (header_msg.get("Message-ID") or "").strip()
            if not message_id or message_id in already_processed:
                continue

            status, body_data = imap.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not body_data or not body_data[0]:
                continue
            full_msg = email.message_from_bytes(body_data[0][1])
            subject = full_msg.get("Subject", "")
            body_text = _get_body_text(full_msg)
            images = _get_images(full_msg)

            received_at = None
            try:
                date_header = full_msg.get("Date")
                if date_header:
                    received_at = parsedate_to_datetime(date_header)
                    if timezone.is_naive(received_at):
                        received_at = timezone.make_aware(received_at)
            except (TypeError, ValueError):
                received_at = None

            candidates.append(
                {
                    "message_id": message_id,
                    "subject": subject,
                    "body_text": body_text,
                    "images": images,
                    "received_at": received_at or timezone.now(),
                }
            )
        return candidates
    finally:
        imap.logout()


def _strip_json_fences(text):
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def extract_events_via_llm(subject, body_text, received_at, images=None):
    """Sends the email text (and any flyer images) to the LLM and returns a
    list of parsed event dicts shaped like scraper.engine.parse_events()'s
    output, ready for upsert_event()."""
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    local_tz = ZoneInfo(settings.EVENTS_LOCAL_TIMEZONE)
    reference_date = received_at.astimezone(local_tz).strftime("%A, %B %d, %Y at %I:%M %p %Z")
    categories = ", ".join(Event.Category.values)

    system_prompt = f"""You extract campus event listings from email text and any attached flyer/poster images, and return ONLY a JSON array — no prose, no markdown code fences.

Some emails are nearly empty text with the actual event details (name, date, time, location) only visible in an attached flyer image — read those images carefully, the same way you'd read the email text.

Each element must be an object with exactly these fields:
- "event_name": string, required
- "location": string, free-text building/room name as written (e.g. "Filene Auditorium", "Collis Common Ground"). Empty string if not mentioned.
- "start_time": ISO 8601 datetime string WITH UTC offset, e.g. "2026-07-20T18:00:00-04:00". Required.
- "end_time": ISO 8601 datetime string WITH UTC offset. If not stated, assume 1 hour after start_time.
- "description": short plain-text summary, empty string if none.
- "category": must be exactly one of: {categories}
- "source_url": a URL mentioned for the event (RSVP link, more-info link), empty string if none.

Rules:
- This email was received on {reference_date} ({settings.EVENTS_LOCAL_TIMEZONE}). Use it to resolve relative dates ("tomorrow", "this Friday", dates without a year, etc), including dates/times written on flyer images. If a time of day is given with no explicit day (e.g. "10PM" in an email sent that afternoon), assume it means later that same day, not a future day, unless the text implies otherwise.
- Only extract things that are genuinely scheduled events with a specific date/time. Ignore generic announcements, deadlines, job postings, digest/newsletter fluff, and anything without an actual event time.
- Skip events whose start_time has already passed relative to the email's received date — this is a forward-looking events feed, not a historical log.
- If nothing in the text or images is a real, upcoming event, return an empty array: []
- Return ONLY the JSON array, nothing else."""

    content = [{"type": "text", "text": f"Subject: {subject}\n\n{body_text[:12000]}"}]
    for image in images or []:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["media_type"],
                    "data": image["data"],
                },
            }
        )

    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    raw_text = _strip_json_fences(raw_text)

    try:
        raw_events = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_events, list):
        return []

    parsed = []
    for raw in raw_events:
        try:
            name = re.sub(r"\s+", " ", str(raw.get("event_name", "")).strip())
            if not name:
                continue
            start_time = date_parser.parse(raw["start_time"])
            if timezone.is_naive(start_time):
                start_time = timezone.make_aware(start_time, local_tz)

            end_raw = raw.get("end_time")
            if end_raw:
                end_time = date_parser.parse(end_raw)
                if timezone.is_naive(end_time):
                    end_time = timezone.make_aware(end_time, local_tz)
            else:
                end_time = start_time + timedelta(hours=1)

            category = raw.get("category", "")
            if category not in Event.Category.values:
                category = Event.Category.CLUB_ORG_MEETING

            parsed.append(
                {
                    "event_name": name,
                    "location": (raw.get("location") or "").strip(),
                    "start_time": start_time,
                    "end_time": end_time,
                    "description": (raw.get("description") or "").strip(),
                    "category": category,
                    "source_url": (raw.get("source_url") or "").strip(),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue

    return dedupe_batch(parsed)


def dedupe_batch(events):
    """Within a single email/LLM response, drop exact repeats of the same
    (normalized name, start_time) pair before they ever hit the DB."""
    seen = set()
    deduped = []
    for item in events:
        key = (item["event_name"].lower(), item["start_time"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def scrape_email_source(source_id):
    source = ScrapeSource.objects.get(pk=source_id)
    if not source.is_active:
        return {"status": "skipped", "message": "Source is inactive"}

    try:
        candidates = fetch_candidate_emails(source)
    except EmailAuthError as exc:
        source.last_scraped_at = timezone.now()
        source.last_scrape_status = ScrapeSource.ScrapeStatus.FAILED
        source.last_scrape_log = str(exc)
        source.save(update_fields=["last_scraped_at", "last_scrape_status", "last_scrape_log"])
        return {"status": "failed", "message": str(exc)}
    except Exception as exc:
        source.last_scraped_at = timezone.now()
        source.last_scrape_status = ScrapeSource.ScrapeStatus.FAILED
        source.last_scrape_log = f"IMAP fetch failed: {exc}"
        source.save(update_fields=["last_scraped_at", "last_scrape_status", "last_scrape_log"])
        return {"status": "failed", "message": str(exc)}

    total_events = 0
    emails_processed = 0
    errors = []

    for candidate in candidates:
        try:
            events = extract_events_via_llm(
                candidate["subject"],
                candidate["body_text"],
                candidate["received_at"],
                images=candidate["images"],
            )
            for item in events:
                upsert_event(item, source, default_review_status=Event.ReviewStatus.PENDING)
            ProcessedEmail.objects.update_or_create(
                scrape_source=source,
                message_id=candidate["message_id"],
                defaults={
                    "subject": candidate["subject"][:500],
                    "received_at": candidate["received_at"],
                    "events_extracted": len(events),
                },
            )
            total_events += len(events)
            emails_processed += 1
        except Exception as exc:
            errors.append(f"{candidate['subject'][:80]!r}: {exc}")

    source.last_scraped_at = timezone.now()
    if errors and emails_processed:
        source.last_scrape_status = ScrapeSource.ScrapeStatus.PARTIAL
    elif errors:
        source.last_scrape_status = ScrapeSource.ScrapeStatus.FAILED
    else:
        source.last_scrape_status = ScrapeSource.ScrapeStatus.SUCCESS
    source.last_scrape_log = (
        f"Scanned {len(candidates)} new emails, extracted {total_events} events."
        if not errors
        else f"Scanned {len(candidates)} new emails, extracted {total_events} events. Errors: {'; '.join(errors)}"
    )
    source.save(update_fields=["last_scraped_at", "last_scrape_status", "last_scrape_log"])

    return {
        "status": source.last_scrape_status,
        "emails_processed": emails_processed,
        "events_extracted": total_events,
        "errors": errors,
    }
