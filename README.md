# TG Summariser

MVP Telegram bot that ingests Telegram channel posts, summarizes them, groups them by AI-generated categories, and learns from user feedback.

## What is included

- Telegram bot interface for one user
- Storage layer with SQLAlchemy
- Scheduler for periodic digests
- Telethon user client wrapper for channel reading
- AI pipeline interface with safe fallback heuristics
- Search and hidden-post flows

## Quick start

1. Create a virtualenv and install dependencies.
2. Copy `.env.example` to `.env`.
3. Fill in Telegram and OpenAI credentials.
4. Run:

```bash
python3 -m src.tg_summariser.main
```

## Notes

- Private channels require a Telegram user session through Telethon.
- The project defaults to SQLite locally and can use Postgres on Railway via `DATABASE_URL`.

## Telethon session for Railway

For Railway, prefer `TELEGRAM_SESSION_STRING` over a local session file.

Generate it locally:

```bash
PYTHONPATH=src python3 -m tg_summariser.session_login
```

The script will ask for your phone, login code, and optional 2FA password, then print a session string you can store in Railway env vars.

## Railway env vars

Required:

- `BOT_TOKEN`
- `DATABASE_URL`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION_STRING`
- `OWNER_TELEGRAM_ID`

Optional:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `AI_PROCESSING_LIMIT_PER_RUN` - max pending posts sent through AI per run, default `20`
- `AI_BATCH_SIZE` - posts classified in one OpenAI request, default `5`
- `AI_MIN_TEXT_LENGTH` - shorter posts use local fallback without API, default `120`
- `AI_MAX_INPUT_CHARS` - max characters sent to OpenAI per post, default `3000`
- `AI_PREFILTER_ENABLED` - local zero-cost prefilter before OpenAI, default `true`
- `AI_PREFILTER_STRICT` - hide posts without positive topic keywords unless the channel has positive feedback, default `true`
- `AI_PREFILTER_POSITIVE_KEYWORDS` - comma-separated topic keywords for strict prefiltering
- `AI_PREFILTER_NEGATIVE_KEYWORDS` - comma-separated promo/noise keywords hidden before OpenAI
- `TELEGRAM_SYNC_DELAY_SECONDS` - delay between Telegram channel reads to reduce flood-wait risk, default `15`
- `TELEGRAM_CHANNEL_SYNC_TIMEOUT_SECONDS` - max time spent on one Telegram channel, default `45`
- `DIGEST_SCHEDULES`
- `DIGEST_MAX_POST_AGE_DAYS` - maximum source-post age included in a digest, default `3`
- `DIGEST_MIN_IMPORTANCE_SCORE` - minimum AI importance required for a digest, default `0.5`
- `TIMEZONE`
- `TGARTICLES_DATABASE_URL` - Postgres URL for importing article candidates from TGArticles
- `TGARTICLES_IMPORT_ENABLED`
- `TGARTICLES_IMPORT_DAYS`
- `TGARTICLES_IMPORT_LIMIT`
- `TGARTICLES_MIN_TEXT_LENGTH`
- `TGARTICLES_IMPORT_SCHEDULES` - comma-separated import times, default `08:30,11:30,14:30,17:30,20:30`
