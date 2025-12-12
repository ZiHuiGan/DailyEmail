import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lambda_function


def test_subject_contains_prefix_and_city():
    os.environ["SUBJECT_PREFIX"] = "[DailyWeatherBot]"

    weather = {
        "city": "Jersey City",
        "temp": 10.0,
        "feels_like": 8.0,
        "desc": "多云",
    }

    subject, body = lambda_function.build_subject_and_body(weather)

    assert "[DailyWeatherBot]" in subject
    assert "Jersey City" in subject
    assert "多云" in body
