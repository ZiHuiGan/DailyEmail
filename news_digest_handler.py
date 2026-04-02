import os
import re
import json
import imaplib
import smtplib
import socket
import email
from email.message import EmailMessage
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# IMAP: fetch newsletters
# ---------------------------------------------------------------------------

def fetch_newsletters(senders: list[str], lookback_hours: int) -> list[dict]:
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    socket.setdefaulttimeout(30)  # fail fast instead of hanging until Lambda timeout
    results = []
    print("Connecting to Gmail IMAP...")
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        print("Connected. Logging in...")
        imap.login(smtp_user, smtp_pass)
        print("Logged in. Selecting INBOX...")
        imap.select("INBOX")
        print("INBOX selected. Searching...")

        since_date = cutoff.strftime("%d-%b-%Y")  # IMAP date format e.g. "31-Mar-2026"
        for sender in senders:
            print(f"Searching for emails from {sender} since {since_date}...")
            _, data = imap.search(None, f'(FROM "{sender}" SINCE "{since_date}")')
            uids = data[0].split()
            for uid in uids:
                _, msg_data = imap.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)

                date_str = parsed.get("Date", "")
                try:
                    msg_date = email.utils.parsedate_to_datetime(date_str)
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    if msg_date < cutoff:
                        continue
                except Exception:
                    pass

                text_content = parse_email_text(raw)
                results.append({
                    "uid": uid.decode(),
                    "subject": parsed.get("Subject", "(no subject)"),
                    "sender": sender,
                    "date": date_str,
                    "text_content": text_content,
                })

    return results


# ---------------------------------------------------------------------------
# Email parsing
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def parse_email_text(raw_bytes: bytes) -> str:
    msg = email.message_from_bytes(raw_bytes)

    plain = None
    html = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                plain = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif ct == "text/html" and html is None:
                html = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text

    if plain:
        return plain.strip()
    if html:
        return _strip_html(html).strip()
    return ""


# ---------------------------------------------------------------------------
# Anthropic API summarization
# ---------------------------------------------------------------------------

def summarize_with_bedrock(newsletter_text: str, source_name: str) -> str:
    import anthropic

    model_id = os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001")
    api_key = os.environ["ANTHROPIC_API_KEY"]

    client = anthropic.Anthropic(api_key=api_key)

    prompt = (
        f"You are an AI news editor. Below is the full text of an AI newsletter from '{source_name}'.\n\n"
        f"Summarize the 3-5 most important AI news items as a numbered list.\n"
        f"Format each item exactly like this:\n"
        f"1. **Bold headline of the news.** One sentence of extra context or significance.\n\n"
        f"Rules:\n"
        f"- The bold part should be a punchy headline (10 words max)\n"
        f"- The sentence after should add context or explain why it matters\n"
        f"- Focus only on significant AI developments, releases, or research\n"
        f"- Skip promotional content, ads, and job postings\n"
        f"- Return only the numbered list, no intro or conclusion\n\n"
        f"Newsletter text:\n{newsletter_text[:8000]}"
    )

    message = client.messages.create(
        model=model_id,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Digest email builder
# ---------------------------------------------------------------------------

def build_digest_subject_and_body(summaries: list[dict]) -> tuple[str, str]:
    prefix = os.getenv("NEWS_DIGEST_PREFIX", "[AI News Digest]")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = len(summaries)
    subject = f"{prefix} {today} ({n} newsletter{'s' if n != 1 else ''})"

    # Convert **bold** markdown to <strong> HTML tags
    def md_to_html(text: str) -> str:
        import re
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    # Build one HTML card per newsletter source
    cards = []
    for item in summaries:
        # Convert numbered lines into <li> elements
        lines = item["summary"].strip().splitlines()
        items_html = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Strip leading "1. ", "2. " etc then apply bold conversion
            line = re.sub(r'^\d+\.\s*', '', line)
            items_html += f"<li style='margin-bottom:10px'>{md_to_html(line)}</li>\n"

        card = f"""
        <div style='margin-bottom:32px; padding:20px; border-left:4px solid #4A90D9; background:#f9f9f9; border-radius:4px'>
            <div style='font-size:13px; color:#888; margin-bottom:4px'>{item['date']}</div>
            <div style='font-size:17px; font-weight:bold; color:#222; margin-bottom:12px'>{item['source_name']}</div>
            <ol style='margin:0; padding-left:20px; color:#333; line-height:1.7'>
                {items_html}
            </ol>
        </div>"""
        cards.append(card)

    cards_html = "\n".join(cards) if cards else "<p>No newsletters found in this period.</p>"

    html_body = f"""
    <html><body style='font-family:Arial,sans-serif; max-width:680px; margin:auto; padding:24px; color:#222'>
        <h1 style='font-size:22px; color:#1a1a1a; border-bottom:2px solid #4A90D9; padding-bottom:8px'>
            🗞 AI News Digest — {today}
        </h1>
        {cards_html}
        <p style='font-size:12px; color:#aaa; margin-top:32px'>
            Delivered by DailyEmail Bot • {n} source{'s' if n != 1 else ''}
        </p>
    </body></html>"""

    return subject, html_body


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

def send_digest_gmail(subject: str, body: str) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")
    to_emails = [e.strip() for e in os.environ["DIGEST_TO_EMAILS"].split(",") if e.strip()]

    msg = EmailMessage()
    msg["From"] = os.environ.get("FROM_EMAIL", smtp_user)
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.set_content("Your email client does not support HTML.")  # plain text fallback
    msg.add_alternative(body, subtype="html")  # HTML version shown by all modern clients

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


# ---------------------------------------------------------------------------
# S3 deduplication
# ---------------------------------------------------------------------------

def get_processed_ids(bucket: str, key: str) -> set[str]:
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return set(json.loads(obj["Body"].read()))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return set()
        raise


def store_processed_ids(bucket: str, key: str, ids: set[str]) -> None:
    import boto3

    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(list(ids)))


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context) -> dict:
    # Check if it's 2pm New York time — handles EDT (UTC-4) and EST (UTC-5) automatically.
    # ZoneInfo("America/New_York") knows DST rules so we don't have to.
    ny_hour = datetime.now(ZoneInfo("America/New_York")).hour
    if ny_hour != 14:  # 14 = 2pm
        print(f"Skipping: current New York hour is {ny_hour}, not 2pm.")
        return {"statusCode": 200, "body": json.dumps({"skipped": True, "ny_hour": ny_hour})}

    senders_raw = os.environ.get("NEWSLETTER_SENDERS", "")
    senders = [s.strip() for s in senders_raw.split(",") if s.strip()]
    lookback_hours = int(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
    s3_bucket = os.getenv("NEWS_S3_BUCKET", "")
    s3_key = "processed_ids.json"

    processed_ids: set[str] = set()
    if s3_bucket:
        processed_ids = get_processed_ids(s3_bucket, s3_key)

    newsletters = fetch_newsletters(senders, lookback_hours)

    new_newsletters = [n for n in newsletters if n["uid"] not in processed_ids]

    summaries = []
    new_ids = set()
    for nl in new_newsletters:
        summary_text = summarize_with_bedrock(nl["text_content"], nl["sender"])
        summaries.append({
            "source_name": nl["sender"],
            "date": nl["date"],
            "subject": nl["subject"],
            "summary": summary_text,
        })
        new_ids.add(nl["uid"])

    if summaries:
        subject, body = build_digest_subject_and_body(summaries)
        send_digest_gmail(subject, body)

    if s3_bucket and new_ids:
        store_processed_ids(s3_bucket, s3_key, processed_ids | new_ids)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "fetched": len(newsletters),
            "new": len(new_newsletters),
            "summarized": len(summaries),
            "digest_sent": len(summaries) > 0,
        }),
    }
