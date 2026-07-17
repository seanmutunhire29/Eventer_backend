"""Tests for Dartmouth Home events HTML parsing."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from scraper.parsers.dartmouth_home import (
    _finalize_times,
    _map_category,
    parse_detail_html,
    parse_list_html,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class DartmouthHomeParserTests(SimpleTestCase):
    def test_parse_list_html_extracts_teasers(self):
        html = (FIXTURES / "dartmouth_list_snippet.html").read_text(encoding="utf-8")
        events = parse_list_html(html)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_name"], "Can AI Think Like a Doctor?")
        self.assertIn("event=81899", events[0]["source_url"])
        self.assertIn("9:00 am", events[0]["time_range"])

    def test_parse_detail_html_uses_json_ld(self):
        html = (FIXTURES / "dartmouth_detail_snippet.html").read_text(encoding="utf-8")
        detail = parse_detail_html(html)
        self.assertEqual(detail["event_name"], "Can AI Think Like a Doctor?")
        self.assertIn("Lebanon Opera House", detail["location"])
        self.assertIsNotNone(detail["start_time"])
        self.assertEqual(detail["start_time"].hour, 9)
        self.assertIn("Lectures", detail["category_raw"])

    def test_map_category_prefers_primary_label(self):
        self.assertEqual(
            _map_category("Lectures & Seminars | Off Campus Event", "club_org_meeting"),
            "academic_lecture",
        )
        self.assertEqual(_map_category("Free Food", "club_org_meeting"), "free_food")

    def test_finalize_times_uses_time_range_end(self):
        start = datetime(2026, 7, 15, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        times = _finalize_times(
            {"time_range": "9:00 am - 11:30 am"},
            {"start_time": start, "time_range": "9:00 am - 11:30 am", "duration": ""},
        )
        self.assertIsNotNone(times)
        _, end = times
        self.assertEqual(end.hour, 11)
        self.assertEqual(end.minute, 30)
