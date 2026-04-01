# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Build a daily AI industry digest bot that fetches AI newsletters from Gmail, summarizes them with AWS Bedrock Claude, and emails insightful digests to subscribers. The weather bot (`lambda_function.py`) is the original foundation; the digest bot (`news_digest_handler.py`) is the evolving core. Development happens in phases — new tools and packages are always welcome if they improve token efficiency, reduce AWS cost, or increase information accuracy.

## Commands

```bash
# Install dev dependencies (pytest only; boto3 is available in Lambda runtime, not locally)
pip install -r requirements.txt

# Run a single test (preferred — faster feedback)
pytest tests/test_news_digest.py::test_lambda_handler_full_flow -v
pytest tests/test_send_email_gmail.py::test_send_email_gmail_builds_subject_and_sends -v

# Run all tests
pytest tests/ -v
```

## Architecture

Two independent Lambda functions, each with its own handler file and EventBridge schedule:

| Handler | Lambda | Purpose |
|---|---|---|
| `lambda_function.py` | `DailyWeatherBot` | Fetches weather → sends daily email via Gmail SMTP or SES |
| `news_digest_handler.py` | `DailyEmailNewsDigest` | Fetches AI newsletters via IMAP → summarizes with Bedrock → sends digest |

**Shared credentials** (`SMTP_USER`, `SMTP_APP_PASSWORD`, `FROM_EMAIL`) are used by both Lambdas for Gmail SMTP sending. The same Gmail account is used for both reading (IMAP) and sending (SMTP).

**Deploy**: GitHub Actions (`.github/workflows/deploy.yml`) zips and deploys both handlers on every push to `main`. Secrets `LAMBDA_FUNCTION_NAME` and `NEWS_LAMBDA_FUNCTION_NAME` point to each Lambda respectively.

### news_digest_handler flow

```
EventBridge → lambda_handler
  → fetch_newsletters()     IMAP4_SSL: search UNSEEN emails from NEWSLETTER_SENDERS
  → [deduplicate via S3]    get_processed_ids() — skipped if NEWS_S3_BUCKET unset
  → parse_email_text()      prefers text/plain; falls back to html.parser stripping
  → summarize_with_bedrock() boto3 bedrock-runtime invoke_model → 3-5 bullet summary
  → build_digest_subject_and_body()
  → send_digest_gmail()     SMTP_SSL port 465
  → store_processed_ids()   write processed UIDs back to S3
```

### Key env vars for news digest Lambda

| Variable | Required | Notes |
|---|---|---|
| `NEWSLETTER_SENDERS` | yes | Comma-separated Gmail addresses to watch |
| `DIGEST_TO_EMAILS` | yes | Comma-separated digest recipients |
| `SMTP_USER` / `SMTP_APP_PASSWORD` / `FROM_EMAIL` | yes | Shared Gmail creds |
| `BEDROCK_MODEL_ID` | no | Default: `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| `BEDROCK_REGION` | no | Defaults to Lambda's region |
| `NEWS_S3_BUCKET` | no | If unset, deduplication is disabled |
| `NEWS_LOOKBACK_HOURS` | no | Default: `24` |

## Code Style & Teaching Notes

- This repo is written as a learning project — code should be readable and well-commented where logic isn't obvious.
- When introducing a new concept or non-trivial pattern, explain **what** it does, **why** it's the right choice, and **how** it works.
- When an error occurs: flag it, explain the possible root causes and why before touching any code, then fix.
- Prefer the simplest stdlib approach first; only add packages if they clearly beat stdlib on token cost, AWS cost, or accuracy.
- `boto3` is not installed locally — mock at the function level (e.g. `patch("news_digest_handler.summarize_with_bedrock", ...)`) rather than patching `boto3.client` directly in tests.
