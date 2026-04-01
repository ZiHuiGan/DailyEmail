import os
import sys
import json
import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_digest_handler


# ---------------------------------------------------------------------------
# parse_email_text
# ---------------------------------------------------------------------------

def _make_plain_email(text: str) -> bytes:
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.com"
    msg["To"] = "reader@example.com"
    return msg.as_bytes()


def _make_html_email(html: str) -> bytes:
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.com"
    msg["To"] = "reader@example.com"
    return msg.as_bytes()


def _make_multipart_email(plain: str, html: str) -> bytes:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.com"
    msg["To"] = "reader@example.com"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_bytes()


def test_parse_email_text_plain():
    raw = _make_plain_email("Hello, world! This is plain text.")
    result = news_digest_handler.parse_email_text(raw)
    assert result == "Hello, world! This is plain text."


def test_parse_email_text_html_fallback():
    raw = _make_html_email("<html><body><p>AI news today</p></body></html>")
    result = news_digest_handler.parse_email_text(raw)
    assert "AI news today" in result


def test_parse_email_text_multipart_prefers_plain():
    raw = _make_multipart_email("plain content", "<p>html content</p>")
    result = news_digest_handler.parse_email_text(raw)
    assert result == "plain content"


# ---------------------------------------------------------------------------
# build_digest_subject_and_body
# ---------------------------------------------------------------------------

def test_build_digest_subject_and_body_default_prefix(monkeypatch):
    monkeypatch.delenv("NEWS_DIGEST_PREFIX", raising=False)
    summaries = [
        {"source_name": "AI Weekly", "date": "Mon, 31 Mar 2026", "subject": "Issue 42", "summary": "• Bullet one"},
        {"source_name": "The Batch", "date": "Mon, 31 Mar 2026", "subject": "Issue 7", "summary": "• Bullet two"},
    ]
    subject, body = news_digest_handler.build_digest_subject_and_body(summaries)
    assert subject.startswith("[AI News Digest]")
    assert "2 newsletters" in subject
    assert "AI Weekly" in body
    assert "• Bullet one" in body
    assert "The Batch" in body


def test_build_digest_subject_single_newsletter(monkeypatch):
    monkeypatch.setenv("NEWS_DIGEST_PREFIX", "[My Digest]")
    summaries = [
        {"source_name": "TLDR AI", "date": "Mon, 31 Mar 2026", "subject": "Today", "summary": "• Item"},
    ]
    subject, _ = news_digest_handler.build_digest_subject_and_body(summaries)
    assert "[My Digest]" in subject
    assert "1 newsletter)" in subject


def test_build_digest_subject_empty(monkeypatch):
    monkeypatch.delenv("NEWS_DIGEST_PREFIX", raising=False)
    subject, body = news_digest_handler.build_digest_subject_and_body([])
    assert "0 newsletters" in subject
    assert "No newsletters found" in body


# ---------------------------------------------------------------------------
# lambda_handler (integration with mocked IMAP, Bedrock, SMTP)
# ---------------------------------------------------------------------------

def _make_raw_email_bytes(subject: str, sender: str, text: str) -> bytes:
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "reader@example.com"
    msg["Date"] = "Mon, 31 Mar 2026 12:00:00 +0000"
    return msg.as_bytes()


def test_lambda_handler_full_flow(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "reader@gmail.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "test pass word")
    monkeypatch.setenv("FROM_EMAIL", "reader@gmail.com")
    monkeypatch.setenv("DIGEST_TO_EMAILS", "dest@example.com")
    monkeypatch.setenv("NEWSLETTER_SENDERS", "ai@weekly.com")
    monkeypatch.setenv("NEWS_LOOKBACK_HOURS", "24")
    monkeypatch.delenv("NEWS_S3_BUCKET", raising=False)

    raw_email = _make_raw_email_bytes("AI Weekly #42", "ai@weekly.com", "Big AI news this week.")

    fake_imap = MagicMock()
    fake_imap.__enter__ = lambda s: fake_imap
    fake_imap.__exit__ = MagicMock(return_value=False)
    fake_imap.login.return_value = ("OK", [])
    fake_imap.select.return_value = ("OK", [])
    fake_imap.search.return_value = ("OK", [b"1"])
    fake_imap.fetch.return_value = ("OK", [(b"1 (RFC822 {123})", raw_email)])

    fake_smtp_server = MagicMock()

    with patch("news_digest_handler.imaplib.IMAP4_SSL", return_value=fake_imap), \
         patch("news_digest_handler.smtplib.SMTP_SSL") as mock_smtp, \
         patch("news_digest_handler.summarize_with_bedrock", return_value="• Big AI news item this week"):
        mock_smtp.return_value.__enter__.return_value = fake_smtp_server
        result = news_digest_handler.lambda_handler({}, None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["fetched"] == 1
    assert body["new"] == 1
    assert body["summarized"] == 1
    assert body["digest_sent"] is True

    fake_smtp_server.send_message.assert_called_once()
    sent_msg = fake_smtp_server.send_message.call_args[0][0]
    assert "[AI News Digest]" in sent_msg["Subject"]
    assert "• Big AI news item this week" in sent_msg.get_payload()


def test_lambda_handler_no_newsletters(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "reader@gmail.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "testpass")
    monkeypatch.setenv("FROM_EMAIL", "reader@gmail.com")
    monkeypatch.setenv("DIGEST_TO_EMAILS", "dest@example.com")
    monkeypatch.setenv("NEWSLETTER_SENDERS", "ai@weekly.com")
    monkeypatch.delenv("NEWS_S3_BUCKET", raising=False)

    fake_imap = MagicMock()
    fake_imap.__enter__ = lambda s: fake_imap
    fake_imap.__exit__ = MagicMock(return_value=False)
    fake_imap.login.return_value = ("OK", [])
    fake_imap.select.return_value = ("OK", [])
    fake_imap.search.return_value = ("OK", [b""])

    with patch("news_digest_handler.imaplib.IMAP4_SSL", return_value=fake_imap), \
         patch("news_digest_handler.smtplib.SMTP_SSL") as mock_smtp:
        result = news_digest_handler.lambda_handler({}, None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["fetched"] == 0
    assert body["digest_sent"] is False
    mock_smtp.assert_not_called()
