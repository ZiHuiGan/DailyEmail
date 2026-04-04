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


def get_weather_description(weather: dict) -> str:
    """Claude writes a single-sentence 'feels like' description."""
    import anthropic

    prompt = (
        "Write exactly 1 sentence describing what it feels like outside and what to wear. "
        "Be warm and conversational.\n\n"
        f"City: {weather['city']} | {weather['temp_f']:.0f}°F, feels like "
        f"{weather['feels_like_f']:.0f}°F | {weather['description']} | "
        f"Humidity {weather['humidity']}% | Wind {weather['wind_mph']:.0f} mph"
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}],
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

def summarize_news(newsletters: list[dict]) -> str:
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
        "You are an AI news editor. Below are AI newsletters from the past 24 hours.\n\n"
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


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _md_bold_to_html(text: str) -> str:
    """Convert **bold** markdown to <strong> HTML."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)


def _news_lines_to_html(text: str) -> str:
    """Convert numbered list text into styled <ol> HTML."""
    if not text or text.strip().lower() == "none":
        return "<p style='color:#888;font-style:italic'>No AI news today.</p>"
    lines = text.strip().splitlines()
    items = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        line = re.sub(r'^\d+\.\s*', '', line)
        items += f"<li style='margin-bottom:12px;line-height:1.6'>{_md_bold_to_html(line)}</li>\n"
    return f"<ol style='padding-left:20px;color:#333'>{items}</ol>" if items else ""


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email(
    tone: str,
    weather: dict,
    weather_desc: str,
    alert: str | None,
    news_html: str = "",
) -> tuple[str, str]:
    now_ny = _get_ny_now()
    date_str = now_ny.strftime("%A, %B %-d")
    label = "Morning" if tone == "morning" else "Evening"
    emoji = "🌅" if tone == "morning" else "🌆"

    subject = f"{emoji} {label} Brief — {weather['city']} — {date_str}"

    alert_html = (
        f'<div style="background:#c0392b;color:white;padding:12px 16px;border-radius:4px;'
        f'margin-bottom:20px;font-weight:bold;font-size:14px">{alert}</div>'
    ) if alert else ""

    # Compact single-line weather stats (replaces the old 4-cell table)
    stats_line = (
        f"{weather['temp_f']:.0f}°F · feels like {weather['feels_like_f']:.0f}°F · "
        f"{weather['description']} · {weather['wind_mph']:.0f} mph wind · {weather['humidity']}% humidity"
    )

    # News section is only included in the evening email
    news_section = ""
    if news_html:
        news_section = f"""
        <h2 style="font-size:17px;color:#1a1a1a;border-bottom:2px solid #4A90D9;padding-bottom:6px;margin-bottom:16px">
            🗞 AI News — Today
        </h2>
        {news_html}
        """

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:660px;margin:auto;padding:24px;color:#222">

        {alert_html}

        <!-- WEATHER SECTION -->
        <div style="font-size:13px;color:#888;margin-bottom:16px">{date_str}</div>

        <p style="font-size:15px;line-height:1.7;background:#fafafa;padding:14px;
                  border-left:4px solid #E8A838;border-radius:4px;margin-bottom:8px">
            {weather_desc}
        </p>

        <p style="font-size:13px;color:#888;margin-bottom:32px">{stats_line}</p>

        {news_section}

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

        # Gate: only run at 9:30am (weather only) or 5:30pm (weather + news) New York time.
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

    # --- Weather (both sends) ---
    print("Fetching weather...")
    weather = get_weather()
    alert = detect_severe_weather(weather["weather_id"], weather["temp_f"], weather["wind_mph"])
    weather_desc = get_weather_description(weather)

    # --- News (evening only — newsletters arrive 5am–4pm, all captured by 5:30pm) ---
    newsletters_fetched = 0
    news_html = ""
    if tone == "evening":
        lookback_hours = float(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
        newsletters = fetch_newsletters(senders, lookback_hours)
        newsletters_fetched = len(newsletters)
        print(f"Fetched {newsletters_fetched} newsletters.")
        if newsletters:
            news_text = summarize_news(newsletters)
            news_html = _news_lines_to_html(news_text)

    # --- Build + send ---
    subject, body = build_email(tone, weather, weather_desc, alert, news_html)
    send_email(subject, body)
    print(f"{tone.capitalize()} brief sent.")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "tone": tone,
            "city": weather["city"],
            "newsletters_fetched": newsletters_fetched,
            "alert": alert,
            "sent": True,
        }),
    }
