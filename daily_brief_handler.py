import os
import re
import json
import imaplib
import smtplib
import socket
import email
import urllib.request
import urllib.parse
from email.message import EmailMessage
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Time helper (isolated for easy mocking in tests)
# ---------------------------------------------------------------------------

def _get_ny_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def get_weather() -> dict:
    api_key = os.environ["WEATHER_API_KEY"]
    city = os.environ["CITY_NAME"]
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={urllib.parse.quote(city)}&appid={api_key}&units=imperial"
    )
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {
        "city": data["name"],
        "temp_f": data["main"]["temp"],
        "feels_like_f": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],
        "wind_mph": data["wind"]["speed"],
        "weather_id": data["weather"][0]["id"],
        "description": data["weather"][0]["description"],
    }


def detect_severe_weather(weather_id: int, temp_f: float, wind_mph: float) -> str | None:
    """Returns an alert string if conditions are dangerous, else None."""
    if 200 <= weather_id <= 232:
        return "⚡ Thunderstorm warning: lightning and heavy rain possible. Avoid open areas."
    if 600 <= weather_id <= 622:
        return "❄️ Winter weather alert: snow or ice expected. Allow extra travel time."
    if weather_id == 781:
        return "🌪️ Tornado warning in effect — stay indoors and away from windows."
    if weather_id in (711, 721, 731, 741, 751, 761, 762):
        return "🌫️ Low visibility: smoke, fog, haze, or dust in the area."
    if temp_f >= 100:
        return f"🌡️ Extreme heat warning: {temp_f:.0f}°F — stay hydrated, limit outdoor exposure."
    if temp_f <= 15:
        return f"🥶 Extreme cold warning: {temp_f:.0f}°F — frostbite risk, minimize time outside."
    if wind_mph >= 40:
        return f"💨 High wind advisory: {wind_mph:.0f} mph — secure loose outdoor objects."
    return None


def get_weather_description(weather: dict, tone: str) -> str:
    """Claude writes a human 'feels like' description in morning or evening tone."""
    import anthropic

    prompts = {
        "morning": (
            "Write a 2-sentence weather briefing for someone starting their day. "
            "Explain what it physically feels like outside — not just numbers. "
            "Tell them what to wear. Be warm and conversational.\n\n"
            f"City: {weather['city']} | {weather['temp_f']:.0f}°F, feels like "
            f"{weather['feels_like_f']:.0f}°F | {weather['description']} | "
            f"Humidity {weather['humidity']}% | Wind {weather['wind_mph']:.0f} mph\n\n"
            "Exactly 2 sentences, no lists."
        ),
        "evening": (
            "Write a 2-sentence weather briefing for someone heading home. "
            "Focus on commute conditions and what to expect stepping outside. "
            "Be warm and conversational.\n\n"
            f"City: {weather['city']} | {weather['temp_f']:.0f}°F, feels like "
            f"{weather['feels_like_f']:.0f}°F | {weather['description']} | "
            f"Humidity {weather['humidity']}% | Wind {weather['wind_mph']:.0f} mph\n\n"
            "Exactly 2 sentences, no lists."
        ),
    }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=120,
        messages=[{"role": "user", "content": prompts[tone]}],
    )
    return msg.content[0].text.strip()


# ---------------------------------------------------------------------------
# Email parsing (IMAP)
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def parse_email_text(raw_bytes: bytes) -> str:
    msg = email.message_from_bytes(raw_bytes)
    plain = html = None
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
        stripper = _HTMLStripper()
        stripper.feed(html)
        return stripper.get_text().strip()
    return ""


def fetch_newsletters(senders: list[str], lookback_hours: float) -> list[dict]:
    """Fetch emails from given senders within the lookback window."""
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    since_date = cutoff.strftime("%d-%b-%Y")

    socket.setdefaulttimeout(30)
    results = []
    print("Connecting to Gmail IMAP...")
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        print("Connected. Logging in...")
        imap.login(smtp_user, smtp_pass)
        print("Logged in. Selecting INBOX...")
        imap.select("INBOX")
        print("INBOX selected. Searching...")

        for sender in senders:
            print(f"Searching from {sender} since {since_date}...")
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
                results.append({
                    "uid": uid.decode(),
                    "subject": parsed.get("Subject", "(no subject)"),
                    "sender": sender,
                    "date": date_str,
                    "text_content": parse_email_text(raw),
                })
    return results


# ---------------------------------------------------------------------------
# AI News summarization
# ---------------------------------------------------------------------------

def summarize_morning_news(newsletters: list[dict]) -> str:
    """
    Feed all newsletters to Claude at once.
    Claude deduplicates overlapping stories and ranks by importance.
    Returns formatted HTML-ready text.
    """
    import anthropic

    combined = ""
    for nl in newsletters:
        combined += f"\n\n=== SOURCE: {nl['sender']} | {nl['date']} ===\n"
        combined += nl["text_content"][:3000]  # cap per newsletter

    prompt = (
        "You are an AI news editor. Below are several AI newsletters from the past 24 hours.\n\n"
        "Your job:\n"
        "1. Identify all unique AI news stories across all sources\n"
        "2. Merge duplicate coverage of the same story into one entry\n"
        "3. Rank stories by importance and urgency (most impactful first)\n"
        "4. Return the top 8 stories as a numbered list\n\n"
        "Format each item exactly like this:\n"
        "1. **Bold punchy headline (10 words max).** One sentence explaining why it matters.\n\n"
        "Rules:\n"
        "- Skip ads, job postings, and promotional content\n"
        "- If multiple sources covered the same story, mention that (e.g. 'Covered by 3 sources')\n"
        "- Return only the numbered list, no intro or conclusion\n\n"
        f"Newsletters:\n{combined[:10000]}"
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def summarize_evening_news(newsletters: list[dict], morning_summary: str) -> tuple[str, str]:
    """
    Summarize afternoon newsletters, separating new stories from ones
    already covered this morning.
    Returns (new_stories_text, redundant_stories_text).
    """
    import anthropic

    if not newsletters:
        return "", ""

    combined = ""
    for nl in newsletters:
        combined += f"\n\n=== SOURCE: {nl['sender']} | {nl['date']} ===\n"
        combined += nl["text_content"][:3000]

    prompt = (
        "You are an AI news editor. Below are two sections:\n"
        "1. MORNING DIGEST: stories already sent to the reader this morning\n"
        "2. NEW NEWSLETTERS: newsletters that arrived after 9:30am today\n\n"
        "Your job:\n"
        "- Identify stories in the new newsletters that are NOT in the morning digest → NEW STORIES\n"
        "- Identify stories that overlap with the morning digest → ALREADY COVERED\n\n"
        "Format your response EXACTLY like this (keep the headers):\n\n"
        "NEW STORIES:\n"
        "1. **Bold headline.** One sentence on why it matters.\n"
        "(list all new stories, ranked by importance)\n\n"
        "ALREADY COVERED:\n"
        "- Brief title of redundant story\n"
        "(list redundant story titles only, one per line)\n\n"
        "If there are no new stories, write 'NEW STORIES:\\nNone'\n"
        "If there are no redundant stories, write 'ALREADY COVERED:\\nNone'\n\n"
        f"MORNING DIGEST:\n{morning_summary}\n\n"
        f"NEW NEWSLETTERS:\n{combined[:8000]}"
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    # Split on the ALREADY COVERED header
    if "ALREADY COVERED:" in raw:
        parts = raw.split("ALREADY COVERED:", 1)
        new_part = parts[0].replace("NEW STORIES:", "").strip()
        redundant_part = parts[1].strip()
    else:
        new_part = raw.replace("NEW STORIES:", "").strip()
        redundant_part = ""

    return new_part, redundant_part


# ---------------------------------------------------------------------------
# S3: store/load morning summary for evening dedup
# ---------------------------------------------------------------------------

def _s3_morning_key(date_str: str) -> str:
    return f"morning_summary_{date_str}.txt"


def load_morning_summary(bucket: str) -> str:
    import boto3
    from botocore.exceptions import ClientError

    date_str = _get_ny_now().strftime("%Y-%m-%d")
    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=bucket, Key=_s3_morning_key(date_str))
        return obj["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return ""
        raise


def store_morning_summary(bucket: str, summary: str) -> None:
    import boto3

    date_str = _get_ny_now().strftime("%Y-%m-%d")
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=_s3_morning_key(date_str),
        Body=summary.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _md_bold_to_html(text: str) -> str:
    """Convert **bold** markdown to <strong> HTML."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)


def _news_lines_to_html(text: str) -> str:
    """Convert numbered list text into styled <ol> HTML."""
    if not text or text.strip().lower() == "none":
        return "<p style='color:#888;font-style:italic'>No new stories in this window.</p>"
    lines = text.strip().splitlines()
    items = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^\d+\.\s*', '', line)
        items += f"<li style='margin-bottom:12px;line-height:1.6'>{_md_bold_to_html(line)}</li>\n"
    return f"<ol style='padding-left:20px;color:#333'>{items}</ol>" if items else ""


def _redundant_lines_to_html(text: str) -> str:
    """Convert the ALREADY COVERED bullet list into a compact archive section."""
    if not text or text.strip().lower() == "none":
        return ""
    lines = [l.strip().lstrip("- ").strip() for l in text.strip().splitlines() if l.strip() and l.strip().lower() != "none"]
    if not lines:
        return ""
    items = "".join(f"<li style='color:#888;font-size:13px'>{l}</li>" for l in lines)
    return f"""
    <div style="margin-top:24px;padding:14px;background:#f5f5f5;border-radius:4px;border-left:4px solid #ccc">
        <div style="font-size:12px;font-weight:bold;color:#999;margin-bottom:8px">
            📦 Already in Morning Brief
        </div>
        <ul style="margin:0;padding-left:18px">{items}</ul>
    </div>"""


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email(
    tone: str,
    weather: dict,
    weather_desc: str,
    alert: str | None,
    news_html: str,
    redundant_html: str = "",
) -> tuple[str, str]:
    now_ny = _get_ny_now()
    date_str = now_ny.strftime("%A, %B %-d")
    label = "Morning" if tone == "morning" else "Evening"
    emoji = "🌅" if tone == "morning" else "🌆"
    news_label = "Last 24 Hours" if tone == "morning" else "Since 9:30am Today"

    subject = f"{emoji} {label} Brief — {weather['city']} — {date_str}"

    alert_html = (
        f'<div style="background:#c0392b;color:white;padding:12px 16px;border-radius:4px;'
        f'margin-bottom:20px;font-weight:bold;font-size:14px">{alert}</div>'
    ) if alert else ""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:660px;margin:auto;padding:24px;color:#222">

        {alert_html}

        <!-- WEATHER SECTION -->
        <h1 style="font-size:20px;color:#1a1a1a;border-bottom:3px solid #E8A838;padding-bottom:8px;margin-bottom:4px">
            {emoji} {label} Brief — {weather['city']}
        </h1>
        <div style="font-size:13px;color:#888;margin-bottom:16px">{date_str}</div>

        <p style="font-size:15px;line-height:1.7;background:#fafafa;padding:14px;
                  border-left:4px solid #E8A838;border-radius:4px;margin-bottom:16px">
            {weather_desc}
        </p>

        <table style="width:100%;border-collapse:collapse;margin-bottom:32px">
            <tr>
                <td style="padding:10px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase">Temp</div>
                    <div style="font-size:24px;font-weight:bold">{weather['temp_f']:.0f}°F</div>
                </td>
                <td style="padding:10px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase">Feels Like</div>
                    <div style="font-size:24px;font-weight:bold;color:#E8A838">{weather['feels_like_f']:.0f}°F</div>
                </td>
                <td style="padding:10px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase">Humidity</div>
                    <div style="font-size:24px;font-weight:bold">{weather['humidity']}%</div>
                </td>
                <td style="padding:10px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase">Wind</div>
                    <div style="font-size:24px;font-weight:bold">{weather['wind_mph']:.0f} mph</div>
                </td>
            </tr>
        </table>

        <!-- AI NEWS SECTION -->
        <h2 style="font-size:17px;color:#1a1a1a;border-bottom:2px solid #4A90D9;padding-bottom:6px;margin-bottom:16px">
            🗞 AI News — {news_label}
        </h2>

        {news_html}
        {redundant_html}

        <p style="font-size:11px;color:#bbb;margin-top:32px;border-top:1px solid #eee;padding-top:12px">
            DailyEmail Bot • {weather['city']} • {label} Brief
        </p>
    </body></html>
    """
    return subject, html


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")
    to_emails = [e.strip() for e in os.environ["DIGEST_TO_EMAILS"].split(",") if e.strip()]

    msg = EmailMessage()
    msg["From"] = os.environ.get("FROM_EMAIL", smtp_user)
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.set_content("Your email client does not support HTML.")
    msg.add_alternative(body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context) -> dict:
    # Allow manual test invocations to bypass the time gate.
    # Pass {"tone": "morning"} or {"tone": "evening"} in the event to force a run.
    if event.get("tone") in ("morning", "evening"):
        tone = event["tone"]
        print(f"Test override: forcing {tone} run.")
    else:
        now_ny = _get_ny_now()
        h, m = now_ny.hour, now_ny.minute

        # Gate: only run at 9:30am or 5:30pm New York time.
        # EventBridge fires at both EDT and EST UTC equivalents;
        # this check ensures only the correct one proceeds.
        if h == 9 and m == 30:
            tone = "morning"
        elif h == 17 and m == 30:
            tone = "evening"
        else:
            print(f"Skipping: NY time is {h:02d}:{m:02d}.")
            return {"statusCode": 200, "body": json.dumps({"skipped": True, "ny_time": f"{h:02d}:{m:02d}"})}

    senders = [s.strip() for s in os.environ.get("NEWSLETTER_SENDERS", "").split(",") if s.strip()]
    s3_bucket = os.getenv("NEWS_S3_BUCKET", "")

    # --- Weather ---
    print("Fetching weather...")
    weather = get_weather()
    alert = detect_severe_weather(weather["weather_id"], weather["temp_f"], weather["wind_mph"])
    weather_desc = get_weather_description(weather, tone)

    # --- News ---
    if tone == "morning":
        lookback_hours = float(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
        newsletters = fetch_newsletters(senders, lookback_hours)
        print(f"Fetched {len(newsletters)} newsletters.")

        if newsletters:
            news_text = summarize_morning_news(newsletters)
            if s3_bucket:
                store_morning_summary(s3_bucket, news_text)
        else:
            news_text = "None"

        news_html = _news_lines_to_html(news_text)
        redundant_html = ""

    else:  # evening
        # Lookback from 9:31am today to now (~8 hours)
        today_931 = now_ny.replace(hour=9, minute=31, second=0, microsecond=0)
        lookback_hours = (now_ny - today_931).total_seconds() / 3600
        newsletters = fetch_newsletters(senders, lookback_hours)
        print(f"Fetched {len(newsletters)} newsletters since 9:31am.")

        morning_summary = load_morning_summary(s3_bucket) if s3_bucket else ""
        if newsletters:
            new_text, redundant_text = summarize_evening_news(newsletters, morning_summary)
        else:
            new_text, redundant_text = "", ""

        news_html = _news_lines_to_html(new_text)
        redundant_html = _redundant_lines_to_html(redundant_text)

    # --- Build + send ---
    subject, body = build_email(tone, weather, weather_desc, alert, news_html, redundant_html)
    send_email(subject, body)
    print(f"{tone.capitalize()} brief sent.")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "tone": tone,
            "city": weather["city"],
            "newsletters_fetched": len(newsletters) if senders else 0,
            "alert": alert,
            "sent": True,
        }),
    }
