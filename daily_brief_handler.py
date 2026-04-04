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


def _weather_emoji(weather_id: int) -> str:
    """Map OpenWeatherMap condition code to a display emoji."""
    if 200 <= weather_id <= 232: return "⛈"
    if 300 <= weather_id <= 321: return "🌦"
    if 500 <= weather_id <= 531: return "🌧"
    if 600 <= weather_id <= 622: return "❄️"
    if 700 <= weather_id <= 781: return "🌫"
    if weather_id == 800:        return "☀️"
    if weather_id == 801:        return "🌤"
    if weather_id == 802:        return "⛅"
    if 803 <= weather_id <= 804: return "☁️"
    return "🌡"


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

def summarize_news(newsletters: list[dict]) -> tuple[str, str]:
    """
    Feed all newsletters to Claude at once.
    Claude deduplicates stories and returns two sections:
      - AI NEWS: top unique stories ranked by importance
      - ARCHIVED: titles of duplicate/redundant stories

    Returns (news_text, archived_text).
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
        "3. Rank unique stories by importance (most impactful first)\n"
        "4. List any redundant/duplicate stories separately\n\n"
        "Format your response EXACTLY like this (keep both headers):\n\n"
        "AI NEWS:\n"
        "1. **Bold punchy headline (10 words max).** One sentence explaining why it matters.\n"
        "(list top 8 unique stories)\n\n"
        "ARCHIVED:\n"
        "- Title of redundant story\n"
        "(one per line — titles only, no descriptions)\n\n"
        "Rules:\n"
        "- Skip ads, job postings, and promotional content\n"
        "- If there are no archived stories, write 'ARCHIVED:\\nNone'\n"
        "- Return only these two sections, no intro or conclusion\n\n"
        f"Newsletters:\n{combined[:10000]}"
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    # Split on the ARCHIVED header to get two sections
    if "ARCHIVED:" in raw:
        parts = raw.split("ARCHIVED:", 1)
        news_part = parts[0].replace("AI NEWS:", "").strip()
        archived_part = parts[1].strip()
    else:
        news_part = raw.replace("AI NEWS:", "").strip()
        archived_part = ""

    return news_part, archived_part


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


def _archived_lines_to_html(text: str) -> str:
    """Convert archived story titles into a compact bulleted list."""
    if not text or text.strip().lower() == "none":
        return ""
    lines = [
        l.strip().lstrip("- ").strip()
        for l in text.strip().splitlines()
        if l.strip() and l.strip().lower() != "none"
    ]
    if not lines:
        return ""
    items = "".join(f"<li style='color:#888;font-size:13px;margin-bottom:4px'>{l}</li>" for l in lines)
    return f"<ul style='margin:0;padding-left:18px'>{items}</ul>"


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def _section_header(title: str, color: str = "#1a1a1a", border: str = "#E8A838") -> str:
    return (
        f'<h2 style="font-size:17px;color:{color};border-bottom:2px solid {border};'
        f'padding-bottom:6px;margin-top:32px;margin-bottom:16px">{title}</h2>'
    )


def build_email(
    tone: str,
    weather: dict,
    alert: str | None,
    news_html: str = "",
    archived_html: str = "",
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

    # Weather stat grid: 4 cells, no outer border, clean labels
    w_emoji = _weather_emoji(weather["weather_id"])
    stat_cell = (
        'style="padding:10px 16px;text-align:center;border:1px solid #eee;border-radius:4px"'
    )
    weather_grid = f"""
    <p style="font-size:15px;margin:0 0 12px 0">{w_emoji} {weather['description'].title()}</p>
    <table style="border-collapse:separate;border-spacing:6px;width:100%;margin-bottom:0">
        <tr>
            <td {stat_cell}>
                <div style="font-size:22px;font-weight:bold">{weather['temp_f']:.0f}°F</div>
                <div style="font-size:11px;color:#999;margin-top:2px">Temp</div>
            </td>
            <td {stat_cell}>
                <div style="font-size:22px;font-weight:bold;color:#E8A838">{weather['feels_like_f']:.0f}°F</div>
                <div style="font-size:11px;color:#999;margin-top:2px">Feels Like</div>
            </td>
            <td {stat_cell}>
                <div style="font-size:22px;font-weight:bold">{weather['wind_mph']:.0f} mph</div>
                <div style="font-size:11px;color:#999;margin-top:2px">Wind</div>
            </td>
            <td {stat_cell}>
                <div style="font-size:22px;font-weight:bold">{weather['humidity']}%</div>
                <div style="font-size:11px;color:#999;margin-top:2px">Humidity</div>
            </td>
        </tr>
    </table>
    """

    # AI News section (evening only)
    news_section = ""
    if news_html:
        news_section = _section_header("AI News", border="#4A90D9") + news_html

    # Archived section (only when there are redundant stories)
    archived_section = ""
    if archived_html:
        archived_section = _section_header("Archived", color="#999", border="#ccc") + archived_html

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:660px;margin:auto;padding:24px;color:#222">

        {alert_html}

        <div style="font-size:13px;color:#888;margin-bottom:16px">{date_str} · {weather['city']}</div>

        {_section_header("Weather")}
        {weather_grid}

        {news_section}
        {archived_section}

        <p style="font-size:11px;color:#bbb;margin-top:32px;border-top:1px solid #eee;padding-top:12px">
            DailyEmail Bot · {weather['city']} · {label} Brief
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

    # --- News (evening only — newsletters arrive 5am–4pm, all captured by 5:30pm) ---
    newsletters_fetched = 0
    news_html = ""
    archived_html = ""
    if tone == "evening":
        lookback_hours = float(os.getenv("NEWS_LOOKBACK_HOURS", "24"))
        newsletters = fetch_newsletters(senders, lookback_hours)
        newsletters_fetched = len(newsletters)
        print(f"Fetched {newsletters_fetched} newsletters.")
        if newsletters:
            news_text, archived_text = summarize_news(newsletters)
            news_html = _news_lines_to_html(news_text)
            archived_html = _archived_lines_to_html(archived_text)

    # --- Build + send ---
    subject, body = build_email(tone, weather, alert, news_html, archived_html)
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
