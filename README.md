# Galaxy Tanta What's On Monitor

Monitors the **What's on** movie list at Galaxy Cinemas Tanta and sends Telegram alerts when a new title appears.

## Automatic checks

The monitor is intended to check twice per day at **11:00 AM and 8:00 PM Egypt local time**. GitHub Actions schedules are UTC, so the workflow includes the relevant UTC hours around Egypt's daylight-saving transition and checks the actual `Africa/Cairo` local hour before running.

## Manual check from Telegram

Send this to the bot from the configured Telegram chat:

```text
/check
```

The Telegram command workflow polls every ~5 minutes. It replies immediately when it sees the command, then runs a live Galaxy check and sends the current What's On list plus any additions/removals.

You can also send `/start` or `/help`.

There is also a **Run workflow** button in GitHub Actions for an immediate manual check.

## GitHub secrets

Create these repository Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Keep the bot token private. If it is ever exposed, revoke it in BotFather and create a new token.

## Files

- `monitor.py` — fetches and compares the movie list.
- `telegram_commands.py` — handles `/check` and `/help` through Telegram polling.
- `.github/workflows/check.yml` — twice-daily monitor + manual workflow.
- `.github/workflows/telegram-command.yml` — Telegram command polling.
- `state.json` — last known movie list (created on first successful check).
- `telegram_state.json` — Telegram update offset (created after the command workflow runs).
