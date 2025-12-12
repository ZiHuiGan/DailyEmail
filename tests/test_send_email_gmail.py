import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lambda_function


def test_send_email_gmail_builds_subject_and_sends():
    os.environ["SMTP_USER"] = "sender@gmail.com"
    os.environ["SMTP_APP_PASSWORD"] = "abcd efgh ijkl mnop"
    os.environ["FROM_EMAIL"] = "sender@gmail.com"
    os.environ["TO_EMAILS"] = "a@test.com,b@test.com"
    os.environ["SUBJECT_PREFIX"] = "[DailyWeatherBot]"

    weather = {"city": "Jersey City", "temp": 1, "feels_like": 0, "desc": "多云"}

    fake_server = MagicMock()

    with patch("lambda_function.smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = fake_server
        lambda_function.send_email_gmail(weather)

    fake_server.send_message.assert_called_once()
    msg = fake_server.send_message.call_args[0][0]
    assert "[DailyWeatherBot]" in msg["Subject"]
    assert "Jersey City" in msg["Subject"]
