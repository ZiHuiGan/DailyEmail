import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lambda_function


def test_choose_provider_gmail():
    os.environ["FROM_EMAIL"] = "zihui.gan.careers@gmail.com"
    assert lambda_function.choose_provider() == "gmail"
