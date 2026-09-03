"""Scraper for https://home.dartmouth.edu/events.

The public calendar is rendered client-side. List data is loaded from
``/events/ajax/search?offset=&limit=``, and detail pages expose Schema.org
JSON-LD (plus DOM fields for category, contact, and time ranges).
"""

from __future__ import annotations

import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.utils import timezone
import requests

from core.models import Event

logger = logging.getLogger(__name__)

BASE_URL = "https://home.dartmouth.edu"
AJAX_SEARCH_PATH = "/events/ajax/search"
EASTERN = ZoneInfo("America/New_York")

DEFAULT_HEADERS = {
    # Browser-like UA avoids Drupal wrapping AJAX JSON in <textarea> HTML.
    "User-Agent": (
        "Mozilla/5.0 (compatible; EventerBot/1.0; +https://eventer.app) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}

# Dartmouth calendar labels → Eventer category slugs.
DARTMOUTH_CATEGORY_MAP = {
    "arts": Event.Category.ARTS_PERFORMANCE,
    "athletics & recreation": Event.Category.SPORTS_ATHLETICS,
    "athletics and recreation": Event.Category.SPORTS_ATHLETICS,
    "clubs & organizations": Event.Category.CLUB_ORG_MEETING,
    "clubs and organizations": Event.Category.CLUB_ORG_MEETING,
    "conferences": Event.Category.CAREER_PROFESSIONAL,
    "dartmouth dialogues": Event.Category.ACADEMIC_LECTURE,
    "diversity, equity and inclusion": Event.Category.VOLUNTEER_COMMUNITY,
    "exhibitions": Event.Category.ARTS_PERFORMANCE,
    "films": Event.Category.ARTS_PERFORMANCE,
    "free food": Event.Category.FREE_FOOD,
    "lectures & seminars": Event.Category.ACADEMIC_LECTURE,
    "lectures and seminars": Event.Category.ACADEMIC_LECTURE,
    "off campus event": Event.Category.CLUB_ORG_MEETING,
    "performances": Event.Category.ARTS_PERFORMANCE,
    "school of arts and sciences": Event.Category.ACADEMIC_LECTURE,
    "service & volunteer": Event.Category.VOLUNTEER_COMMUNITY,
    "service and volunteer": Event.Category.VOLUNTEER_COMMUNITY,
    "spiritual & worship": Event.Category.RELIGIOUS_SPIRITUAL,
    "spiritual and worship": Event.Category.RELIGIOUS_SPIRITUAL,
    "workshops & training": Event.Category.CAREER_PROFESSIONAL,
    "workshops and training": Event.Category.CAREER_PROFESSIONAL,
    "health": Event.Category.HEALTH_WELLNESS,
    "wellness": Event.Category.HEALTH_WELLNESS,
}

TIME_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    r"\s*[-–—]\s*"
    r"(?P<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"(?P<hours>\d+)\s*hours?|(?P<minutes>\d+)\s*minutes?",
    re.IGNORECASE,
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def _absolute_url(href: str) -> str:
    if not href:
        return ""
    return urljoin(BASE_URL, href)


def _clean_detail_url(href: str) -> str:
    """Strip list pagination query params from an event detail link."""
    absolute = _absolute_url(href)
    if not absolute:
        return ""
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    event_ids = query.get("event")
    if not event_ids:
        return absolute.split("#")[0]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            urlencode({"event": event_ids[0]}),
            "",
        )
    )


def _map_category(raw: str, default: str) -> str:
    if not raw:
        return default
    # Categories may be pipe-separated: "Lectures & Seminars | Off Campus Event"
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    for part in parts:
        key = part.lower()
        if key in DARTMOUTH_CATEGORY_MAP and key != "off campus event":
            return DARTMOUTH_CATEGORY_MAP[key]
    for part in parts:
        key = part.lower()
        if key in DARTMOUTH_CATEGORY_MAP:
            return DARTMOUTH_CATEGORY_MAP[key]
        alias = Event.CATEGORY_ALIASES.get(key)
        if alias:
            return alias
    return default


def _aware(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, EASTERN)
    return dt.astimezone(EASTERN)


def _parse_time_on_date(time_str: str, day: datetime) -> datetime | None:
    try:
        parsed = date_parser.parse(time_str, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    combined = day.replace(
        hour=parsed.hour,
        minute=parsed.minute,
        second=0,
        microsecond=0,
    )
    return _aware(combined)


def _end_from_duration(start: datetime, duration_raw: str) -> datetime | None:
    if not duration_raw:
        return None
    hours = 0
    minutes = 0
    for match in DURATION_RE.finditer(duration_raw):
        if match.group("hours"):
            hours += int(match.group("hours"))
        if match.group("minutes"):
            minutes += int(match.group("minutes"))
    if hours or minutes:
        return start + timedelta(hours=hours, minutes=minutes)
    return None


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    return html.unescape(text)


def parse_list_html(content_html: str) -> list[dict]:
    soup = BeautifulSoup(content_html, "lxml")
    events = []
    for teaser in soup.select(".event-teaser"):
        link = teaser.select_one(".event-teaser__title-link")
        if not link:
            continue
        name = link.get_text(strip=True)
        if not name:
            continue
        events.append(
            {
                "event_name": html.unescape(name),
                "source_url": _clean_detail_url(link.get("href", "")),
                "description": html.unescape(
                    teaser.select_one(".event-teaser__summary").get_text(" ", strip=True)
                    if teaser.select_one(".event-teaser__summary")
                    else ""
                ),
                "time_range": (
                    teaser.select_one(".event-teaser__time").get_text(" ", strip=True)
                    if teaser.select_one(".event-teaser__time")
                    else ""
                ),
                "date_day": (
                    teaser.select_one(".event-teaser__date-day").get_text(strip=True)
                    if teaser.select_one(".event-teaser__date-day")
                    else ""
                ),
                "date_month": (
                    teaser.select_one(".event-teaser__date-month").get_text(strip=True)
                    if teaser.select_one(".event-teaser__date-month")
                    else ""
                ),
            }
        )
    return events


def _extract_json_ld_event(soup: BeautifulSoup) -> dict | None:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Event":
                return item
    return None


def parse_detail_html(detail_html: str) -> dict:
    soup = BeautifulSoup(detail_html, "lxml")
    ld = _extract_json_ld_event(soup) or {}

    location = ""
    loc = ld.get("location")
    if isinstance(loc, dict):
        location = loc.get("name") or loc.get("address") or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("streetAddress") or ""
    elif isinstance(loc, str):
        location = loc
    if not location:
        loc_el = soup.select_one(
            ".news-event--details__group--location .news-event--details__group-text"
        )
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

    category_el = soup.select_one(".news-event--category")
    category_raw = category_el.get_text(" ", strip=True) if category_el else ""

    email_el = soup.select_one(".news-event--info__contact--email")
    contact_email = email_el.get_text(strip=True) if email_el else ""

    time_el = soup.select_one(".news-event--time")
    time_range = time_el.get_text(" ", strip=True) if time_el else ""

    date_el = soup.select_one(".news-event--date")
    date_text = date_el.get_text(" ", strip=True) if date_el else ""

    description = _strip_html(ld.get("description") or "")
    if not description:
        about = _strip_html(ld.get("about") or "")
        summary_el = soup.select_one(".news-event--summary")
        description = about or (summary_el.get_text(" ", strip=True) if summary_el else "")

    start_time = None
    if ld.get("startDate"):
        try:
            start_time = _aware(date_parser.isoparse(ld["startDate"]))
        except (ValueError, TypeError, OverflowError):
            start_time = None

    return {
        "location": location.strip() if isinstance(location, str) else "",
        "category_raw": category_raw,
        "contact_email": contact_email,
        "time_range": time_range,
        "date_text": date_text,
        "description": description,
        "start_time": start_time,
        "duration": ld.get("duration") or "",
        "event_name": ld.get("name") or "",
        "source_url": ld.get("url") or "",
    }


def _times_from_list_teaser(item: dict) -> tuple[datetime | None, datetime | None]:
    """Best-effort start/end from list card date fragments + time range."""
    day_raw = item.get("date_day") or ""
    month_raw = item.get("date_month") or ""
    time_range = item.get("time_range") or ""
    if not (day_raw and month_raw and time_range):
        return None, None

    now = timezone.now().astimezone(EASTERN)
    try:
        day = date_parser.parse(f"{month_raw} {day_raw} {now.year}", fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None, None
    day = day.replace(tzinfo=None)
    # If the inferred date is far in the past, roll to next year (year-end wrap).
    aware_day = _aware(day.replace(hour=12))
    if aware_day < now - timedelta(days=30):
        day = day.replace(year=now.year + 1)

    match = TIME_RANGE_RE.search(time_range)
    if not match:
        start = _parse_time_on_date(time_range, day)
        return start, (start + timedelta(hours=1) if start else None)

    start = _parse_time_on_date(match.group("start"), day)
    end = _parse_time_on_date(match.group("end"), day)
    if start and end and end < start:
        end = end + timedelta(days=1)
    return start, end


def _finalize_times(list_item: dict, detail: dict) -> tuple[datetime, datetime] | None:
    start = detail.get("start_time")
    end = None
    time_range = detail.get("time_range") or list_item.get("time_range") or ""

    if start and time_range:
        match = TIME_RANGE_RE.search(time_range)
        if match:
            local_day = start.astimezone(EASTERN).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            end = _parse_time_on_date(match.group("end"), local_day)
            if end and end < start:
                end = end + timedelta(days=1)

    if start and not end:
        end = _end_from_duration(start, detail.get("duration") or "")

    if not start:
        start, end = _times_from_list_teaser(list_item)

    if not start:
        return None
    if not end:
        end = start + timedelta(hours=1)
    return start, end


def _parse_ajax_payload(response: requests.Response) -> list:
    """Parse Drupal AJAX responses (raw JSON or <textarea>-wrapped JSON)."""
    text = response.text.strip()
    if not text:
        return []
    try:
        payload = response.json()
    except ValueError:
        soup = BeautifulSoup(text, "lxml")
        textarea = soup.select_one("textarea")
        if not textarea:
            raise
        payload = json.loads(textarea.get_text())
    if isinstance(payload, dict):
        return [payload]
    return payload if isinstance(payload, list) else []


def fetch_search_page(session: requests.Session, offset: int, limit: int) -> list[dict]:
    response = session.get(
        urljoin(BASE_URL, AJAX_SEARCH_PATH),
        params={"offset": offset, "limit": limit},
        timeout=30,
    )
    response.raise_for_status()
    payload = _parse_ajax_payload(response)
    content = ""
    for command in payload:
        if command.get("command") == "eventsContent":
            content = command.get("content") or ""
            break
    if not content:
        return []
    return parse_list_html(content)


def fetch_detail(session: requests.Session, url: str) -> dict:
    response = session.get(
        url,
        timeout=30,
        headers={**DEFAULT_HEADERS, "Accept": "text/html"},
    )
    response.raise_for_status()
    return parse_detail_html(response.text)


def fetch_dartmouth_events(selector_config: dict | None = None) -> list[dict]:
    """Fetch and normalize Dartmouth Home calendar events for upsert."""
    config = selector_config or {}
    page_limit = int(config.get("page_limit", 50))
    max_events = int(config.get("max_events", 500))
    enrich_details = bool(config.get("enrich_details", True))
    max_workers = int(config.get("detail_workers", 8))
    default_category = config.get("default_category", Event.Category.CLUB_ORG_MEETING)
    if default_category not in Event.Category.values:
        default_category = Event.Category.CLUB_ORG_MEETING

    session = _session()
    listed: list[dict] = []
    offset = 0
    while len(listed) < max_events:
        batch = fetch_search_page(session, offset=offset, limit=page_limit)
        if not batch:
            break
        listed.extend(batch)
        if len(batch) < page_limit:
            break
        offset += page_limit
    listed = listed[:max_events]

    details_by_url: dict[str, dict] = {}
    if enrich_details:
        urls = [item["source_url"] for item in listed if item.get("source_url")]

        def _load(url: str) -> tuple[str, dict | None, str | None]:
            try:
                # Separate session per worker is safer with urllib3.
                return url, fetch_detail(_session(), url), None
            except Exception as exc:  # noqa: BLE001 - collect per-URL errors
                return url, None, str(exc)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_load, url) for url in urls]
            for future in as_completed(futures):
                url, detail, error = future.result()
                if detail:
                    details_by_url[url] = detail
                elif error:
                    logger.warning("Dartmouth detail fetch failed for %s: %s", url, error)

    parsed: list[dict] = []
    for item in listed:
        detail = details_by_url.get(item.get("source_url") or "", {})
        times = _finalize_times(item, detail)
        if not times:
            continue
        start_time, end_time = times

        category = _map_category(detail.get("category_raw", ""), default_category)
        description = detail.get("description") or item.get("description") or ""
        location = detail.get("location") or ""
        source_url = detail.get("source_url") or item.get("source_url") or BASE_URL + "/events"
        name = html.unescape(detail.get("event_name") or item["event_name"])

        other_info = {}
        if detail.get("contact_email"):
            other_info["contact_email"] = detail["contact_email"]
        if category == Event.Category.FREE_FOOD:
            other_info["has_food"] = True

        parsed.append(
            {
                "event_name": name[:255],
                "location": location,
                "start_time": start_time.astimezone(ZoneInfo("UTC")),
                "end_time": end_time.astimezone(ZoneInfo("UTC")),
                "description": description,
                "category": category,
                "source_url": source_url,
                "other_info": other_info,
            }
        )
    return parsed
