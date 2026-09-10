# Galaxy Cinemas Tanta — What's On Monitor

Checks https://tanta.galaxy-cinema.com/ twice daily and sends a Telegram alert when a movie title appears in the **What's on** section that was not present in the previous check.

## Architecture

- GitHub Actions: runs the check twice per day.
- Python + BeautifulSoup: fetches the Tanta homepage and extracts the `h3` movie titles under `What's on`.
- `state.json`: stores the last successful snapshot.
- Telegram Bot API: sends an alert only when new titles appear.

The monitor deliberately watches the **What's on** section, not showtimes.

## One-time setup

### 1. Create a Telegram bot

On Telegram, open **@BotFather** and send:

`/newbot`

Follow the prompts. BotFather gives you a token that looks like:

`123456789:AA...`

Keep it private.

### 2. Start your bot

Open the new bot's Telegram chat and send it:

`/start`

### 3. Get your Telegram chat ID

Open this URL in a browser, replacing `BOT_TOKEN` with your token:

`https://api.telegram.org/botBOT_TOKEN/getUpdates`

Find:

`"chat":{"id":123456789,...}`

That number is your `TELEGRAM_CHAT_ID`.

If `getUpdates` is empty, send `/start` to the bot again and refresh the URL.

### 4. Create a GitHub repository

Create a new GitHub repository, for example:

`galaxy-tanta-monitor`

Upload all files from this folder, including `.github/workflows/check.yml`.

### 5. Add the two GitHub Actions secrets

In your repo:

**Settings → Secrets and variables → Actions → New repository secret**

Add:

- `TELEGRAM_BOT_TOKEN` = your BotFather token
- `TELEGRAM_CHAT_ID` = your numeric chat ID

Do not put the token in the source code.

### 6. Test it immediately

Go to:

**Actions → Check Galaxy Tanta What's On → Run workflow**

The first run creates the baseline. Because this starter configuration has `SEND_INITIAL_SNAPSHOT=true`, it also sends you the current list.

After that, alerts are only sent when a title is newly detected.

## Schedule

The workflow runs at:

- 07:00 UTC
- 17:00 UTC

That is 10:00 AM and 8:00 PM Egypt time while Egypt is UTC+3.

GitHub Actions scheduled workflows can occasionally start a few minutes late.

## What counts as "new"?

A title is considered new if it exists in the current `What's on` section but was absent from the previous successful snapshot.

A movie disappearing is recorded but does not trigger an alert by itself.

## Important

The site's content can change structure. If Galaxy changes the HTML around the `What's on` cards, `monitor.py` may need a small parser adjustment.

The current parser is intentionally based on the actual page structure: the live page has a `What's on` heading followed by movie title headings. 
