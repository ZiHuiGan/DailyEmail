# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

Build a daily AI industry digest bot that sends two emails per day (9:30am and 5:30pm New York time), each containing a weather section and an AI news section. `daily_brief_handler.py` is the active core — it replaces all earlier handlers. Development happens in phases — new tools and packages are always welcome if they improve token efficiency, reduce AWS cost, or increase information accuracy.

## Commands

```bash
# Install dev dependencies (pytest only; boto3 is available in Lambda runtime, not locally)
pip install -r requirements.txt

# Run a single test (preferred — faster feedback)
pytest tests/test_daily_brief.py::test_lambda_handler_morning_send -v

# Run all tests
pytest tests/ -v
```

## Architecture

One active Lambda (`DailyBriefBot`) driven by `daily_brief_handler.py`, triggered 4x/day by EventBridge:

```
EventBridge (4 cron rules) → DailyBriefBot Lambda
  → get_weather()             OpenWeatherMap API
  → detect_severe_weather()   condition code + temp/wind thresholds
  → get_weather_description() Anthropic Claude, 2-sentence "feels like"
  → fetch_newsletters()       IMAP4_SSL, Gmail inbox, date-filtered
  → summarize_morning_news()  Claude deduplicates + ranks all sources together
  → summarize_evening_news()  Claude separates NEW vs ALREADY COVERED
  → load/store_morning_summary() S3: morning_summary_YYYY-MM-DD.txt
  → build_email()             HTML email: weather table + news list
  → send_email()              SMTP_SSL port 465
```

**Morning (9:30am):** 24h newsletter lookback, Claude deduplicates overlapping stories across all sources and ranks by importance. Morning summary stored to S3.

**Evening (5:30pm):** 9:31am–5:30pm lookback. Claude compares against morning summary and separates new stories from redundant ones. Redundant stories shown in a "📦 Already in Morning Brief" archive section.

**EDT/EST handling:** EventBridge fires at 4 UTC times (13:30, 14:30, 21:30, 22:30). Lambda gates on NY hour/minute using `ZoneInfo("America/New_York")` — only the correct one proceeds.

**Test override:** Pass `{"tone": "morning"}` or `{"tone": "evening"}` in the Lambda test event to bypass the time gate and force a real send.

### Key env vars (DailyBriefBot Lambda)

| Variable | Required | Notes |
|---|---|---|
| `WEATHER_API_KEY` | yes | OpenWeatherMap API key |
| `CITY_NAME` | yes | e.g. `New York` |
| `NEWSLETTER_SENDERS` | yes | Comma-separated Gmail addresses to watch |
| `DIGEST_TO_EMAILS` | yes | Comma-separated recipients |
| `SMTP_USER` / `SMTP_APP_PASSWORD` / `FROM_EMAIL` | yes | Gmail credentials |
| `ANTHROPIC_API_KEY` | yes | Direct Anthropic API (not Bedrock) |
| `NEWS_S3_BUCKET` | no | If unset, evening dedup is disabled |
| `NEWS_LOOKBACK_HOURS` | no | Default: `24` (morning only) |
| `ANTHROPIC_MODEL_ID` | no | Default: `claude-haiku-4-5-20251001` |

### IAM permissions required on Lambda role
- `s3:GetObject` + `s3:PutObject` on `arn:aws:s3:::NEWS_S3_BUCKET/*`

## Code Style & Teaching Notes

- This repo is written as a learning project — code should be readable and well-commented where logic isn't obvious.
- When introducing a new concept or non-trivial pattern, explain **what** it does, **why** it's the right choice, and **how** it works.
- When an error occurs: **stop and identify the root cause first** before touching any code. Ask: what is the system actually telling us? Trace back to the origin of the problem — don't fix symptoms. Most bugs have one real cause hiding behind the error message (e.g. a timeout that looks like a crash is really a missing socket timeout; an AccessDenied that looks like a code bug is really a missing IAM policy).
- Prefer the simplest stdlib approach first; only add packages if they clearly beat stdlib on token cost, AWS cost, or accuracy.
- `boto3` is not installed locally — mock at the function level (e.g. `patch("daily_brief_handler.load_morning_summary", ...)`) rather than patching `boto3.client` directly in tests.

## Lessons Learned (Project History)

These are real problems encountered and solved — useful context when debugging similar issues:

| Problem | Root Cause | Fix |
|---|---|---|
| Lambda silently timed out (no logs) | `imaplib` had no socket timeout — hung forever | `socket.setdefaulttimeout(30)` |
| IMAP hung at `select("INBOX")` | No `SINCE` filter — Gmail scanned entire inbox | Added `SINCE "{date}"` to search query |
| Bedrock `ResourceNotFoundException` | Model IDs deprecated or wrong format | Switched to direct Anthropic API entirely |
| Bedrock `ThrottlingException` | New AWS accounts have very low default quotas | Switched to direct Anthropic API entirely |
| Deploy failed with `ResourceNotFoundException` | `LAMBDA_FUNCTION_NAME` secret pointed to a deleted Lambda | Removed that deploy step from `deploy.yml` |
| Lambda skipped during test | Test event was `{}` — time gate fired before tone override check | Pass `{"tone": "morning"}` in test event |
| S3 `AccessDenied` on `PutObject` | Lambda role had no S3 policy | Added inline IAM policy with `s3:GetObject` + `s3:PutObject` |
| Multiple newsletters, duplicate stories | Each source summarized separately | Feed all newsletters to Claude at once for unified dedup + ranking |
