import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# 1️⃣ 手动把项目根目录加入 sys.path：.../EmailBot/DailyEmail
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 2️⃣ 现在再导入 lambda_function 就不会报错了
import lambda_function


def test_send_email_subject_contains_prefix_and_city():
    # 准备一个假的天气数据
    weather = {
        "city": "Jersey City",
        "temp": 10.0,
        "feels_like": 8.0,
        "desc": "多云",
    }

    # 设置环境变量，避免函数里访问环境变量时报错
    os.environ["TO_EMAILS"] = "test@example.com"
    os.environ["FROM_EMAIL"] = "sender@example.com"

    # 准备一个假的 SES client，用来拦截 send_email 调用
    fake_ses_client = MagicMock()

    # 用 patch 把 lambda_function 里的 boto3.client 替换成我们的假对象
    with patch("lambda_function.boto3.client", return_value=fake_ses_client):
        lambda_function.send_email(weather)

    # 断言 SES 的 send_email 确实被调用了一次
    fake_ses_client.send_email.assert_called_once()

    # 取出这次调用时传入的参数
    _, kwargs = fake_ses_client.send_email.call_args
    subject = kwargs["Message"]["Subject"]["Data"]

    # 标题应该包含前缀和城市名
    assert "[DailyWeatherBot]" in subject
    assert weather["city"] in subject
