import os
import json
import urllib.request
from datetime import datetime
import urllib.parse

import boto3  # AWS Python SDK


def get_weather():
    """
    从 OpenWeatherMap 获取当前天气信息
    """
    api_key = os.environ["WEATHER_API_KEY"]
    city = os.environ["CITY_NAME"]

    # 对城市名做 URL 编码，比如 "Jersey City" -> "Jersey%20City"
    city_encoded = urllib.parse.quote(city)

    base_url = "https://api.openweathermap.org/data/2.5/weather"
    query = (
        f"?q={city_encoded}"
        f"&appid={api_key}"
        f"&units=metric"
        f"&lang=zh_cn"
    )
    url = base_url + query

    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode("utf-8"))

    temp = data["main"]["temp"]                # 当前温度
    feels_like = data["main"]["feels_like"]    # 体感温度
    desc = data["weather"][0]["description"]   # 天气描述，例如“多云”

    return {
        "city": city,
        "temp": temp,
        "feels_like": feels_like,
        "desc": desc,
    }

def send_email(weather):
    """
    使用 AWS SES 发送邮件，支持多个收件人
    """
    ses = boto3.client("ses")

    # 从环境变量中读取多个邮箱，用逗号分隔
    to_emails_raw = os.environ["TO_EMAILS"]
    to_emails = [
        email.strip()
        for email in to_emails_raw.split(",")
        if email.strip()
    ]

    from_email = os.environ["FROM_EMAIL"]

    today_str = datetime.now().strftime("%Y-%m-%d")

    SUBJECT_PREFIX = "[DailyWeatherBot]"  # 以后有别的 bot 就换别的前缀
    
    subject = f"{SUBJECT_PREFIX} 今日天气 - {city}"

    body_text = (
        f"早上好！\n\n"
        f"今天 {weather['city']} 的天气情况：\n"
        f"- 天气：{weather['desc']}\n"
        f"- 当前温度：{weather['temp']}°C\n"
        f"- 体感温度：{weather['feels_like']}°C\n\n"
        f"祝你有愉快和充实的一天！"
    )

    ses.send_email(
        Source=from_email,
        Destination={"ToAddresses": to_emails},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body_text, "Charset": "UTF-8"}
            },
        },
    )


def lambda_handler(event, context):
    """
    Lambda 的入口函数
    """
    weather = get_weather()
    send_email(weather)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"message": "Email sent", "weather": weather},
            ensure_ascii=False,
        ),
    }
