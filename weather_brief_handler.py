import os
import json
import smtplib
import urllib.request
import urllib.parse
from email.message import EmailMessage
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Weather data
# ---------------------------------------------------------------------------

def get_weather() -> dict:
    api_key = os.environ["WEATHER_API_KEY"]
    city = os.environ["CITY_NAME"]
    city_encoded = urllib.parse.quote(city)

    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city_encoded}&appid={api_key}&units=imperial"
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
        "weather_main": data["weather"][0]["main"],
        "description": data["weather"][0]["description"],
    }


# ---------------------------------------------------------------------------
# Severe weather detection (free tier only — no One Call API)
# Weather condition codes: https://openweathermap.org/weather-conditions
# ---------------------------------------------------------------------------

def detect_severe_weather(weather_id: int, temp_f: float, wind_mph: float) -> str | None:
    if 200 <= weather_id <= 232:
        return "Thunderstorm warning: lightning and heavy rain possible. Avoid open areas."
    if 600 <= weather_id <= 622:
        return "Winter weather alert: snow or ice expected. Allow extra travel time."
    if weather_id == 781:
        return "Tornado warning in effect — stay indoors and away from windows."
    if weather_id in (711, 721, 731, 741, 751, 761, 762):
        return "Low visibility conditions: smoke, fog, haze, or dust in the area."
    if temp_f >= 100:
        return f"Extreme heat warning: {temp_f:.0f}°F — stay hydrated and limit outdoor exposure."
    if temp_f <= 15:
        return f"Extreme cold warning: {temp_f:.0f}°F — frostbite risk, minimize time outside."
    if wind_mph >= 40:
        return f"High wind advisory: {wind_mph:.0f} mph — secure loose outdoor objects."
    return None


# ---------------------------------------------------------------------------
# Claude: human "feels like" description
# ---------------------------------------------------------------------------

def get_claude_description(weather: dict, tone: str) -> str:
    import anthropic

    prompts = {
        "morning": (
            "You are writing a 2-sentence weather briefing for someone about to start their day. "
            "Explain what the weather physically feels like — not just the numbers. "
            "Tell them what to wear or expect. Be warm and conversational.\n\n"
            f"City: {weather['city']}\n"
            f"Temperature: {weather['temp_f']:.0f}°F, feels like {weather['feels_like_f']:.0f}°F\n"
            f"Conditions: {weather['description']}\n"
            f"Humidity: {weather['humidity']}%, Wind: {weather['wind_mph']:.0f} mph\n\n"
            "Write exactly 2 sentences. No bullet points, no lists."
        ),
        "evening": (
            "You are writing a 2-sentence weather briefing for someone heading home at the end of the day. "
            "Focus on commute conditions and what to expect stepping outside. Be warm and conversational.\n\n"
            f"City: {weather['city']}\n"
            f"Temperature: {weather['temp_f']:.0f}°F, feels like {weather['feels_like_f']:.0f}°F\n"
            f"Conditions: {weather['description']}\n"
            f"Humidity: {weather['humidity']}%, Wind: {weather['wind_mph']:.0f} mph\n\n"
            "Write exactly 2 sentences. No bullet points, no lists."
        ),
    }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
        max_tokens=120,
        messages=[{"role": "user", "content": prompts[tone]}],
    )
    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email(weather: dict, description: str, alert: str | None, tone: str) -> tuple[str, str]:
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    date_str = now_ny.strftime("%A, %B %-d")  # e.g. "Thursday, April 2"
    label = "Morning" if tone == "morning" else "Evening"
    emoji = "🌅" if tone == "morning" else "🌆"

    subject = f"{emoji} {label} Weather — {weather['city']} — {date_str}"

    alert_html = (
        f'<div style="background:#c0392b;color:white;padding:12px 16px;'
        f'border-radius:4px;margin-bottom:20px;font-weight:bold;font-size:14px">'
        f'⚠️ {alert}</div>'
    ) if alert else ""

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:24px;color:#222">

        {alert_html}

        <h1 style="font-size:20px;color:#1a1a1a;border-bottom:3px solid #E8A838;padding-bottom:8px;margin-bottom:16px">
            {emoji} {label} Weather Brief — {weather['city']}
        </h1>
        <div style="font-size:13px;color:#888;margin-bottom:20px">{date_str}</div>

        <p style="font-size:16px;line-height:1.7;color:#222;background:#fafafa;
                  padding:16px;border-left:4px solid #E8A838;border-radius:4px;margin-bottom:24px">
            {description}
        </p>

        <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
            <tr>
                <td style="padding:12px;text-align:center;border:1px solid #eee;border-radius:4px">
                    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px">Temp</div>
                    <div style="font-size:26px;font-weight:bold;color:#1a1a1a">{weather['temp_f']:.0f}°F</div>
                </td>
                <td style="padding:12px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px">Feels Like</div>
                    <div style="font-size:26px;font-weight:bold;color:#E8A838">{weather['feels_like_f']:.0f}°F</div>
                </td>
                <td style="padding:12px;text-align:center;border:1px solid #eee">
                    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px">Humidity</div>
                    <div style="font-size:26px;font-weight:bold;color:#1a1a1a">{weather['humidity']}%</div>
                </td>
                <td style="padding:12px;text-align:center;border:1px solid #eee;border-radius:4px">
                    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px">Wind</div>
                    <div style="font-size:26px;font-weight:bold;color:#1a1a1a">{weather['wind_mph']:.0f} mph</div>
                </td>
            </tr>
        </table>

        <p style="font-size:12px;color:#bbb;margin-top:24px;border-top:1px solid #eee;padding-top:12px">
            DailyEmail Bot • {weather['city']} • {label} Brief • {date_str}
        </p>
    </body></html>
    """
    return subject, html_body


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

def send_weather_email(subject: str, body: str) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")
    to_emails = [e.strip() for e in os.environ["WEATHER_RECIPIENTS"].split(",") if e.strip()]

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
# Time helper (testable)
# ---------------------------------------------------------------------------

def _get_ny_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context) -> dict:
    now_ny = _get_ny_now()
    h, m = now_ny.hour, now_ny.minute

    # Fire at 9:30am and 5:30pm New York time.
    # EventBridge fires at both possible UTC times (EDT and EST);
    # the check below ensures only one actually runs.
    if h == 9 and m == 30:
        tone = "morning"
    elif h == 17 and m == 30:
        tone = "evening"
    else:
        print(f"Skipping: NY time is {h:02d}:{m:02d}, not a send window.")
        return {"statusCode": 200, "body": json.dumps({"skipped": True, "ny_time": f"{h:02d}:{m:02d}"})}

    print(f"Running {tone} weather brief...")
    weather = get_weather()
    print(f"Weather fetched: {weather['temp_f']:.0f}°F, {weather['description']}")

    alert = detect_severe_weather(weather["weather_id"], weather["temp_f"], weather["wind_mph"])
    if alert:
        print(f"Severe weather detected: {alert}")

    description = get_claude_description(weather, tone)
    print(f"Claude description: {description}")

    subject, body = build_email(weather, description, alert, tone)
    send_weather_email(subject, body)
    print(f"{tone.capitalize()} weather email sent.")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "tone": tone,
            "city": weather["city"],
            "temp_f": weather["temp_f"],
            "alert": alert,
            "sent": True,
        }),
    }
