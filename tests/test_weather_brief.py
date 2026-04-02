import os
import sys
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import weather_brief_handler as wbh


# ---------------------------------------------------------------------------
# detect_severe_weather
# ---------------------------------------------------------------------------

def test_no_alert_clear_day():
    assert wbh.detect_severe_weather(800, 72.0, 5.0) is None

def test_thunderstorm_alert():
    result = wbh.detect_severe_weather(210, 70.0, 10.0)
    assert result is not None and "thunderstorm" in result.lower()

def test_snow_alert():
    result = wbh.detect_severe_weather(601, 30.0, 5.0)
    assert result is not None and ("snow" in result.lower() or "winter" in result.lower())

def test_extreme_heat_alert():
    result = wbh.detect_severe_weather(800, 102.0, 5.0)
    assert result is not None and "heat" in result.lower()

def test_extreme_cold_alert():
    result = wbh.detect_severe_weather(800, 10.0, 5.0)
    assert result is not None and "cold" in result.lower()

def test_high_wind_alert():
    result = wbh.detect_severe_weather(800, 72.0, 45.0)
    assert result is not None and "wind" in result.lower()


# ---------------------------------------------------------------------------
# build_email
# ---------------------------------------------------------------------------

def _sample_weather():
    return {
        "city": "New York",
        "temp_f": 55.0,
        "feels_like_f": 50.0,
        "humidity": 60,
        "wind_mph": 10.0,
        "weather_id": 800,
        "weather_main": "Clear",
        "description": "clear sky",
    }

def test_build_email_morning_no_alert():
    subject, html = wbh.build_email(_sample_weather(), "Nice morning.", None, "morning")
    assert "Morning" in subject
    assert "New York" in subject
    assert "Nice morning." in html
    assert "c0392b" not in html  # no red alert box

def test_build_email_evening_with_alert():
    subject, html = wbh.build_email(_sample_weather(), "Cool evening.", "High wind advisory.", "evening")
    assert "Evening" in subject
    assert "High wind advisory." in html
    assert "c0392b" in html  # red alert box present

def test_build_email_shows_feels_like():
    _, html = wbh.build_email(_sample_weather(), "Desc.", None, "morning")
    assert "50" in html  # feels_like_f


# ---------------------------------------------------------------------------
# lambda_handler — skip logic
# ---------------------------------------------------------------------------

def test_lambda_handler_skips_wrong_hour(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "x@gmail.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "pass")
    monkeypatch.setenv("FROM_EMAIL", "x@gmail.com")
    monkeypatch.setenv("WEATHER_RECIPIENTS", "y@gmail.com")
    monkeypatch.setenv("WEATHER_API_KEY", "key")
    monkeypatch.setenv("CITY_NAME", "New York")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

    fake_time = datetime(2026, 4, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("weather_brief_handler._get_ny_now", return_value=fake_time):
        result = wbh.lambda_handler({}, None)

    body = json.loads(result["body"])
    assert body["skipped"] is True


def test_lambda_handler_morning_send(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "x@gmail.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "pass")
    monkeypatch.setenv("FROM_EMAIL", "x@gmail.com")
    monkeypatch.setenv("WEATHER_RECIPIENTS", "y@gmail.com")
    monkeypatch.setenv("WEATHER_API_KEY", "key")
    monkeypatch.setenv("CITY_NAME", "New York")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

    fake_time = datetime(2026, 4, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    fake_weather = {
        "city": "New York", "temp_f": 60.0, "feels_like_f": 55.0,
        "humidity": 50, "wind_mph": 8.0, "weather_id": 800,
        "weather_main": "Clear", "description": "clear sky",
    }
    fake_smtp = MagicMock()

    with patch("weather_brief_handler._get_ny_now", return_value=fake_time), \
         patch("weather_brief_handler.get_weather", return_value=fake_weather), \
         patch("weather_brief_handler.get_claude_description", return_value="Nice morning out there."), \
         patch("weather_brief_handler.smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = fake_smtp
        result = wbh.lambda_handler({}, None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["tone"] == "morning"
    assert body["sent"] is True
    fake_smtp.send_message.assert_called_once()
