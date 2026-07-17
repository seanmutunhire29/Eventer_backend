from datetime import timedelta
import re
from pathlib import Path

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from django.conf import settings
from django.db import transaction
from django.utils import timezone
import requests

from core.models import BuildingAlias, Event, ScrapeSource
from scraper.parsers.dartmouth_home import fetch_dartmouth_events


def resolve_building(location, aliases=None):
    if not location:
        return None, None
    cleaned = location.strip()
    if aliases is None:
        aliases = list(BuildingAlias.objects.select_related("building"))

    for alias in aliases:
        if alias.alias.strip().lower() == cleaned.lower():
            return alias.building, None

    # Prefer the longest alias that appears as a whole word in the location
    # (e.g. "Sudikoff back patio" → "Sudikoff", but not "Hop" inside "Shop").
    best = None
    for candidate in aliases:
        alias_text = candidate.alias.strip()
        if len(alias_text) < 3:
            continue
        if re.search(rf"\b{re.escape(alias_text)}\b", cleaned, flags=re.IGNORECASE):
            if best is None or len(alias_text) > len(best.alias):
                best = candidate
    if best:
        return best.building, None
    return None, cleaned


def parse_events(html, selector_config):
    soup = BeautifulSoup(html, "lxml")
    container_sel = selector_config.get("container", ".event")
    containers = soup.select(container_sel)
    parsed = []

    for container in containers:
        def text(sel):
            if not sel:
                return ""
            el = container.select_one(sel)
            return el.get_text(strip=True) if el else ""

        def href(sel):
            if not sel:
                return ""
            el = container.select_one(sel)
            return el.get("href", "") if el else ""

        name = text(selector_config.get("event_name"))
        if not name:
            continue

        start_raw = text(selector_config.get("start_time"))
        end_raw = text(selector_config.get("end_time")) or start_raw
        location = text(selector_config.get("location"))
        description = text(selector_config.get("description"))
        category_raw = text(selector_config.get("category")) or selector_config.get(
            "default_category", "club_org_meeting"
        )
        source_url = href(selector_config.get("source_url")) or text(
            selector_config.get("source_url")
        )

        try:
            start_time = date_parser.parse(start_raw, fuzzy=True)
            end_time = date_parser.parse(end_raw, fuzzy=True)
            if timezone.is_naive(start_time):
                start_time = timezone.make_aware(start_time)
            if timezone.is_naive(end_time):
                end_time = timezone.make_aware(end_time)
        except (ValueError, TypeError):
            continue

        category = Event.CATEGORY_ALIASES.get(category_raw.lower(), category_raw)
        if category not in Event.Category.values:
            category = Event.Category.CLUB_ORG_MEETING

        parsed.append(
            {
                "event_name": name,
                "location": location,
                "start_time": start_time,
                "end_time": end_time,
                "description": description,
                "category": category,
                "source_url": source_url,
                "other_info": {},
            }
        )
    return parsed


def fetch_parsed_events(source):
    """Return normalized event dicts for a scrape source."""
    config = source.selector_config or {}
    parser_name = config.get("parser")
    if parser_name == "dartmouth_home" or "home.dartmouth.edu/events" in (source.url or ""):
        return fetch_dartmouth_events(config)

    if source.url.startswith("file://"):
        file_path = Path(source.url.replace("file://", "", 1))
        if not file_path.is_absolute():
            file_path = settings.BASE_DIR / file_path
        html = file_path.read_text(encoding="utf-8")
    else:
        response = requests.get(
            source.url,
            timeout=30,
            headers={"User-Agent": "EventerBot/1.0 (+https://eventer.app)"},
        )
        response.raise_for_status()
        html = response.text
    return parse_events(html, config)


def apply_lifecycle(source, seen_event_ids):
    stale = Event.objects.filter(scrape_source=source, is_active=True).exclude(
        id__in=seen_event_ids
    )
    for event in stale:
        event.missed_scrape_count += 1
        if event.missed_scrape_count >= 2:
            event.is_active = False
        event.save(update_fields=["missed_scrape_count", "is_active", "updated_at"])

    cutoff = timezone.now() - timedelta(days=7)
    Event.objects.filter(is_active=False, end_time__lt=cutoff).delete()


def _upsert_parsed_events(source, parsed_events):
    seen_event_ids = []
    errors = []
    created_or_updated = 0
    aliases = list(BuildingAlias.objects.select_related("building"))

    with transaction.atomic():
        for item in parsed_events:
            try:
                building, unresolved = resolve_building(item["location"], aliases=aliases)
                defaults = {
                    "end_time": item["end_time"],
                    "description": item["description"],
                    "category": item["category"],
                    "source_url": item["source_url"] or source.url,
                    "scrape_source": source,
                    "building": building,
                    "unresolved_location": unresolved,
                    "is_active": True,
                    "missed_scrape_count": 0,
                    "other_info": item.get("other_info") or {},
                }
                if building:
                    event, _ = Event.objects.update_or_create(
                        event_name=item["event_name"],
                        building=building,
                        start_time=item["start_time"],
                        defaults=defaults,
                    )
                else:
                    event, created = Event.objects.get_or_create(
                        event_name=item["event_name"],
                        start_time=item["start_time"],
                        scrape_source=source,
                        defaults=defaults,
                    )
                    if not created:
                        for key, value in defaults.items():
                            setattr(event, key, value)
                        event.save()
                seen_event_ids.append(event.id)
                created_or_updated += 1
            except Exception as exc:
                errors.append(f"{item.get('event_name', '?')}: {exc}")

        apply_lifecycle(source, seen_event_ids)

        source.last_scraped_at = timezone.now()
        if errors and created_or_updated:
            source.last_scrape_status = ScrapeSource.ScrapeStatus.PARTIAL
        elif errors:
            source.last_scrape_status = ScrapeSource.ScrapeStatus.FAILED
        else:
            source.last_scrape_status = ScrapeSource.ScrapeStatus.SUCCESS
        source.last_scrape_log = (
            f"Processed {created_or_updated} events."
            if not errors
            else f"Processed {created_or_updated} events. Errors: {'; '.join(errors)}"
        )
        source.save(
            update_fields=["last_scraped_at", "last_scrape_status", "last_scrape_log"]
        )

    return {
        "status": source.last_scrape_status,
        "processed": created_or_updated,
        "errors": errors,
    }


def scrape_source(source_id):
    source = ScrapeSource.objects.get(pk=source_id)
    if not source.is_active:
        return {"status": "skipped", "message": "Source is inactive"}

    try:
        # Network I/O stays outside the DB transaction.
        parsed_events = fetch_parsed_events(source)
    except Exception as exc:
        source.last_scraped_at = timezone.now()
        source.last_scrape_status = ScrapeSource.ScrapeStatus.FAILED
        source.last_scrape_log = str(exc)
        source.save(
            update_fields=["last_scraped_at", "last_scrape_status", "last_scrape_log"]
        )
        return {"status": "failed", "message": str(exc)}

    return _upsert_parsed_events(source, parsed_events)
