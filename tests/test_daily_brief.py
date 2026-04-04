"""
Tests for daily_brief_handler.py

Run a single test:
    pytest tests/test_daily_brief.py::test_no_alert_clear_day -v

Run all daily brief tests:
    pytest tests/test_daily_brief.py -v
"""
import email as email_lib
import json
import os
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import daily_brief_handler as dbh


# ---------------------------------------------------------------------------
# detect_severe_weather
# ---------------------------------------------------------------------------

def test_no_alert_clear_day():
    assert dbh.detect_severe_weather(800, 72.0, 5.0) is None

def test_thunderstorm_alert():
    result = dbh.detect_severe_weather(210, 70.0, 5.0)
    assert result is not None and "thunderstorm" in result.lower()

def test_snow_alert():
    result = dbh.detect_severe_weather(601, 30.0, 5.0)
    assert result is not None and ("snow" in result.lower() or "winter" in result.lower())

def test_tornado_alert():
    result = dbh.detect_severe_weather(781, 70.0, 5.0)
    assert result is not None and "tornado" in result.lower()

def test_extreme_heat_alert():
    result = dbh.detect_severe_weather(800, 102.0, 5.0)
    assert result is not None and "heat" in result.lower()

def test_extreme_cold_alert():
    result = dbh.detect_severe_weather(800, 10.0, 5.0)
    assert result is not None and "cold" in result.lower()

def test_high_wind_alert():
    result = dbh.detect_severe_weather(800, 72.0, 45.0)
    assert result is not None and "wind" in result.lower()

def test_boundary_below_thunderstorm():
    assert dbh.detect_severe_weather(199, 72.0, 5.0) is None

def test_boundary_above_snow():
    assert dbh.detect_severe_weather(623, 72.0, 5.0) is None


# ---------------------------------------------------------------------------
# _weather_emoji
# ---------------------------------------------------------------------------

def test_weather_emoji_clear():
    assert dbh._weather_emoji(800) == "☀️"

def test_weather_emoji_thunderstorm():
    assert dbh._weather_emoji(210) == "⛈"

def test_weather_emoji_snow():
    assert dbh._weather_emoji(601) == "❄️"

def test_weather_emoji_clouds():
    assert dbh._weather_emoji(802) == "⛅"


# ---------------------------------------------------------------------------
# parse_email_text
# ---------------------------------------------------------------------------

def _make_plain_email(text: str) -> bytes:
    msg = MIMEText(text, "plain", "utf-8")
    return msg.as_bytes()

def _make_html_email(html: str) -> bytes:
    msg = MIMEText(html, "html", "utf-8")
    return msg.as_bytes()

def _make_multipart_email(plain: str, html: str) -> bytes:
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_bytes()

def test_parse_plain_text():
    raw = _make_plain_email("Hello world newsletter")
    assert dbh.parse_email_text(raw) == "Hello world newsletter"

def test_parse_html_email():
    raw = _make_html_email("<html><body><p>Hello from HTML</p></body></html>")
    assert "Hello from HTML" in dbh.parse_email_text(raw)

def test_parse_prefers_plain_over_html():
    raw = _make_multipart_email("Plain text version", "<p>HTML version</p>")
    result = dbh.parse_email_text(raw)
    assert "Plain text version" in result
    assert "HTML version" not in result

def test_parse_empty_email():
    msg = email_lib.message.Message()
    assert dbh.parse_email_text(msg.as_bytes()) == ""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def test_md_bold_to_html():
    result = dbh._md_bold_to_html("This is **important** news.")
    assert result == "This is <strong>important</strong> news."

def test_md_bold_multiple():
    result = dbh._md_bold_to_html("**First.** Some context. **Second.** More context.")
    assert "<strong>First.</strong>" in result
    assert "<strong>Second.</strong>" in result

def test_news_lines_to_html_numbered_list():
    text = "1. **Big AI story.** Explains why it matters.\n2. **Another story.** More context."
    result = dbh._news_lines_to_html(text)
    assert "<ol" in result
    assert "<strong>Big AI story.</strong>" in result
    assert result.count("<li") == 2

def test_news_lines_to_html_none_text():
    result = dbh._news_lines_to_html("None")
    assert "No AI news today" in result
    assert "<ol" not in result

def test_news_lines_to_html_empty():
    assert "No AI news today" in dbh._news_lines_to_html("")

def test_archived_lines_to_html_empty():
    assert dbh._archived_lines_to_html("") == ""
    assert dbh._archived_lines_to_html("None") == ""

def test_archived_lines_to_html_with_items():
    text = "- GPT-5 released\n- Anthropic funding round"
    result = dbh._archived_lines_to_html(text)
    assert "GPT-5 released" in result
    assert "Anthropic funding round" in result
    assert "<ul" in result


# ---------------------------------------------------------------------------
# build_email
# ---------------------------------------------------------------------------

def _sample_weather():
    return {
        "city": "New York",
        "temp_f": 62.0,
        "feels_like_f": 58.0,
        "humidity": 55,
        "wind_mph": 12.0,
        "weather_id": 800,
        "description": "clear sky",
    }

def test_build_email_morning_subject():
    subject, _ = dbh.build_email("morning", _sample_weather(), None)
    assert "Morning" in subject
    assert "New York" in subject

def test_build_email_evening_subject():
    subject, _ = dbh.build_email("evening", _sample_weather(), None)
    assert "Evening" in subject

def test_build_email_no_duplicate_title():
    # Body must not contain an <h1> — subject line is the only title
    _, html = dbh.build_email("morning", _sample_weather(), None)
    assert "<h1" not in html

def test_build_email_no_alert_no_red_box():
    _, html = dbh.build_email("morning", _sample_weather(), None)
    assert "c0392b" not in html

def test_build_email_with_alert_shows_red_box():
    _, html = dbh.build_email("morning", _sample_weather(), "⚡ Thunderstorm warning!")
    assert "c0392b" in html
    assert "Thunderstorm warning" in html

def test_build_email_weather_section_header():
    _, html = dbh.build_email("morning", _sample_weather(), None)
    assert "Weather" in html

def test_build_email_weather_stats_present():
    _, html = dbh.build_email("morning", _sample_weather(), None)
    assert "62" in html        # temp
    assert "58" in html        # feels like
    assert "12" in html        # wind
    assert "55" in html        # humidity
    assert "Clear Sky" in html or "clear sky" in html.lower()

def test_build_email_morning_no_news_section():
    # Morning: no news_html → AI News section absent
    _, html = dbh.build_email("morning", _sample_weather(), None)
    assert "AI News" not in html

def test_build_email_evening_has_news_section():
    _, html = dbh.build_email("evening", _sample_weather(), None, "<p>Top story</p>")
    assert "AI News" in html
    assert "Top story" in html

def test_build_email_archived_section_shown():
    archived = dbh._archived_lines_to_html("- GPT-5 released")
    _, html = dbh.build_email("evening", _sample_weather(), None, "<p>News</p>", archived)
    assert "Archived" in html
    assert "GPT-5 released" in html

def test_build_email_no_archived_section_when_empty():
    _, html = dbh.build_email("evening", _sample_weather(), None, "<p>News</p>", "")
    assert "Archived" not in html


# ---------------------------------------------------------------------------
# lambda_handler
# ---------------------------------------------------------------------------

def _set_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "x@gmail.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "pass")
    monkeypatch.setenv("FROM_EMAIL", "x@gmail.com")
    monkeypatch.setenv("DIGEST_TO_EMAILS", "y@gmail.com")
    monkeypatch.setenv("WEATHER_API_KEY", "key")
    monkeypatch.setenv("CITY_NAME", "New York")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("NEWSLETTER_SENDERS", "news@example.com")


def test_lambda_handler_skips_wrong_hour(monkeypatch):
    _set_env(monkeypatch)
    fake_time = datetime(2026, 4, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("daily_brief_handler._get_ny_now", return_value=fake_time):
        result = dbh.lambda_handler({}, None)
    body = json.loads(result["body"])
    assert body["skipped"] is True
    assert body["ny_time"] == "10:00"


def test_lambda_handler_morning_send(monkeypatch):
    """Morning: weather only — fetch_newsletters must NOT be called."""
    _set_env(monkeypatch)
    fake_time = datetime(2026, 4, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    fake_weather = {
        "city": "New York", "temp_f": 60.0, "feels_like_f": 55.0,
        "humidity": 50, "wind_mph": 8.0, "weather_id": 800, "description": "clear sky",
    }
    fake_smtp = MagicMock()

    with patch("daily_brief_handler._get_ny_now", return_value=fake_time), \
         patch("daily_brief_handler.get_weather", return_value=fake_weather), \
         patch("daily_brief_handler.fetch_newsletters") as mock_fetch, \
         patch("daily_brief_handler.smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = fake_smtp
        result = dbh.lambda_handler({}, None)

    body = json.loads(result["body"])
    assert body["tone"] == "morning"
    assert body["newsletters_fetched"] == 0
    assert body["sent"] is True
    mock_fetch.assert_not_called()
    fake_smtp.send_message.assert_called_once()


def test_lambda_handler_evening_with_newsletters(monkeypatch):
    """Evening: fetches newsletters, summarize_news returns (news, archived)."""
    _set_env(monkeypatch)
    fake_time = datetime(2026, 4, 2, 17, 30, tzinfo=ZoneInfo("America/New_York"))
    fake_weather = {
        "city": "New York", "temp_f": 58.0, "feels_like_f": 53.0,
        "humidity": 65, "wind_mph": 7.0, "weather_id": 800, "description": "clear sky",
    }
    fake_newsletter = [{
        "uid": "1", "subject": "AI Daily", "sender": "news@example.com",
        "date": "Thu, 2 Apr 2026 16:00:00 +0000",
        "text_content": "GPT-5 was released today.",
    }]
    fake_smtp = MagicMock()

    with patch("daily_brief_handler._get_ny_now", return_value=fake_time), \
         patch("daily_brief_handler.get_weather", return_value=fake_weather), \
         patch("daily_brief_handler.fetch_newsletters", return_value=fake_newsletter), \
         patch("daily_brief_handler.summarize_news", return_value=("1. **GPT-5 Released.** Big deal.", "- Old story")), \
         patch("daily_brief_handler.smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = fake_smtp
        result = dbh.lambda_handler({}, None)

    body = json.loads(result["body"])
    assert body["tone"] == "evening"
    assert body["newsletters_fetched"] == 1
    assert body["sent"] is True
    fake_smtp.send_message.assert_called_once()


def test_lambda_handler_evening_no_newsletters(monkeypatch):
    """Evening with no newsletters: still sends (weather-only evening email)."""
    _set_env(monkeypatch)
    fake_time = datetime(2026, 4, 2, 17, 30, tzinfo=ZoneInfo("America/New_York"))
    fake_weather = {
        "city": "New York", "temp_f": 58.0, "feels_like_f": 53.0,
        "humidity": 65, "wind_mph": 7.0, "weather_id": 800, "description": "clear sky",
    }
    fake_smtp = MagicMock()

    with patch("daily_brief_handler._get_ny_now", return_value=fake_time), \
         patch("daily_brief_handler.get_weather", return_value=fake_weather), \
         patch("daily_brief_handler.fetch_newsletters", return_value=[]), \
         patch("daily_brief_handler.smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = fake_smtp
        result = dbh.lambda_handler({}, None)

    body = json.loads(result["body"])
    assert body["tone"] == "evening"
    assert body["newsletters_fetched"] == 0
    assert body["sent"] is True
    fake_smtp.send_message.assert_called_once()
