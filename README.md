# Samo

Polls the [Making Sense](https://www.samharris.org/podcasts/making-sense) podcast
RSS feed and sends an [ntfy](https://ntfy.sh) push notification for each new,
unheard episode. Designed to run unattended as a cron job.

## How it works

- Reads the feed and extracts each episode's audio enclosure.
- Compares episode IDs against `heard.json` (the set of episodes already
  notified).
- Pushes a notification per unheard episode (title + download link), then
  records those IDs in `heard.json`. If everything's already heard, it sends a
  single "all caught up" heartbeat instead.

## Setup

1. Install dependencies (Python 3, in a virtualenv):

   ```bash
   pip install feedparser requests
   ```

2. Create a `.env` file (gitignored) with:

   ```
   FEED_URL=https://rss.samharris.org/feed/<your-feed-id>
   NTFY_TOKEN=tk_your_ntfy_access_token
   NTFY_TOPIC=your-ntfy-topic
   NTFY_EMAIL=you@example.com
   ```

   Shell/cron environment variables override values in `.env` if both are set.

3. Create `heard.json` (gitignored) to start tracking. An empty list means
   every current episode counts as unheard and will be notified on the first
   run:

   ```json
   []
   ```

## Running

```bash
python samo.py
```

### As a cron job

```cron
*/30 * * * * /path/to/venv/bin/python /path/to/sam_agentic/samo.py
```

`heard.json` is per-environment state and is not tracked in git, so each machine
keeps its own record of which episodes it has seen.
