import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

import smtplib
from email.message import EmailMessage


def get_weather():
    api_key = os.environ["WEATHER_API_KEY"]
    city = os.environ["CITY_NAME"]

    city_encoded = urllib.parse.quote(city)

    base_url = "https://api.openweathermap.org/data/2.5/weather"
    url = (
        f"{base_url}?q={city_encoded}"
        f"&appid={api_key}&units=metric&lang=zh_cn"
    )

    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode("utf-8"))

    return {
        "city": city,
        "temp": data["main"]["temp"],
        "feels_like": data["main"]["feels_like"],
        "desc": data["weather"][0]["description"],
    }


def build_subject_and_body(weather: dict) -> tuple[str, str]:
    prefix = os.getenv("SUBJECT_PREFIX", "[DailyWeatherBot]")
    city = weather["city"]
    subject = f"{prefix} 今日天气 - {city}"

    body_text = (
        f"早上好！\n\n"
        f"今天 {weather['city']} 的天气情况：\n"
        f"- 天气：{weather['desc']}\n"
        f"- 当前温度：{weather['temp']}°C\n"
        f"- 体感温度：{weather['feels_like']}°C\n\n"
        f"祝你有愉快和充实的一天！"
    )
    return subject, body_text


def send_email_gmail(weather: dict) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_APP_PASSWORD"].replace(" ", "")
    to_emails = [e.strip() for e in os.environ["TO_EMAILS"].split(",") if e.strip()]

    subject, body_text = build_subject_and_body(weather)

    msg = EmailMessage()
    msg["From"] = os.environ.get("FROM_EMAIL", smtp_user)
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.set_content(body_text)

    # Gmail SMTP: SSL 465
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


def send_email_ses(weather: dict) -> None:
    import boto3

    ses = boto3.client("ses")
    to_emails = [e.strip() for e in os.environ["TO_EMAILS"].split(",") if e.strip()]
    from_email = os.environ["FROM_EMAIL"]

    subject, body_text = build_subject_and_body(weather)

    # 也可以加 Html 版本，这里先保持最简单（Text）
    ses.send_email(
        Source=from_email,
        Destination={"ToAddresses": to_emails},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
        },
        ReplyToAddresses=[from_email],
    )


def choose_provider() -> str:
    """
    不改邮箱地址的前提下：
    - FROM_EMAIL 是 gmail.com -> 用 Gmail SMTP（解决 DMARC fail）
    - 未来换成你自己的域名 -> 自动走 SES
    """
    from_email = os.environ.get("FROM_EMAIL", "").lower().strip()
    if from_email.endswith("@gmail.com"):
        return "gmail"
    return "ses"


def lambda_handler(event, context):
    weather = get_weather()
    provider = choose_provider()

    if provider == "gmail":
        send_email_gmail(weather)
    else:
        send_email_ses(weather)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Email sent", "provider": provider, "weather": weather}, ensure_ascii=False),
    }
