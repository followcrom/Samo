# Samo

Polls the [Making Sense](https://www.samharris.org/podcasts/making-sense) podcast
RSS feed and sends an [ntfy](https://ntfy.sh) push notification (and email) for
each new, unheard episode. Designed to run unattended as a cron job.

## How it works

Samo tracks two sets of episode IDs in `heard.json`:

- **`notified`** — episodes it has already pushed a notification for.
- **`heard`** — episodes you have confirmed listening to (see *Marking episodes
  heard* below).

On each run it:

1. Reads the feed and extracts each episode's audio enclosure.
2. Ingests any "mark heard" emails that have arrived since the last run (see
   below) and adds them to the `heard` set.
3. Pushes a notification per **un-notified** episode (title + download link +
   a "Mark heard" action), records those IDs in `notified`, and forwards the
   notification to email via ntfy.
4. If there are no un-notified episodes, it sends a single **"All quiet from
   Sam"** heartbeat instead (push + email), including the most recent episode
   title so you can see the feed is being read.

`heard.json` is per-environment state and is **not** tracked in git, so each
machine keeps its own record.

## Marking episodes heard

Each new-episode notification carries a `mailto:` action/link addressed to a
dedicated mailbox (default `samheard@followcrom.com`) with a subject like
`heard <episode-id>`:

- **In the ntfy app** — tap the **"Mark heard"** button on the notification.
- **In the forwarded email** — tap the **Mark heard** `mailto:` link.

That email is delivered by Postfix on the VM into a local mbox
(`/var/mail/samheard`). On the next run, Samo scans that mbox for `heard <id>`
subjects, moves those episodes into the `heard` set, and **deletes** the
processed messages. You normally never touch the mailbox by hand.

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

   Optionally override the mark-heard address and mailbox (defaults shown):

   ```
   HEARD_ADDR=samheard@followcrom.com
   HEARD_MAILBOX=/var/mail/samheard
   ```

   Shell/cron environment variables override values in `.env` if both are set.

3. Create `heard.json` (gitignored) to start tracking. An empty list means
   every current episode counts as unheard and will be notified on the first
   run:

   ```json
   []
   ```

   To avoid a flood of notifications on a new machine, copy an existing
   `heard.json` over instead.

## Mail integration (VM)

The mark-heard loop needs the VM to **receive** mail for the `samheard` address
and deliver it to a local mbox. On the production box this is already wired up;
the requirements are:

- **DNS** — the `MX` record for the domain points at the VM, and the VM's IP has
  a matching `PTR` (reverse DNS). For `followcrom.com` this is
  `MX → mail.followcrom.com → <vm-ip>`, with the PTR set to `mail.followcrom.com`.
- **Postfix** — installed, listening on port 25, with the domain in
  `mydestination` (so the address is treated as **local** delivery), and using
  default **mbox** format (`/var/mail/<user>`, which is what Samo reads).
- **A `samheard` user** — the address delivers to a local user of that name. If
  it doesn't exist, Postfix rejects the mail as *"User unknown"* and nothing
  lands. Create it as a no-login system account:

  ```bash
  useradd -r -s /usr/sbin/nologin samheard
  ```

Test that delivery works:

```bash
printf 'Subject: heard testping\n\ntest\n' | sendmail -i samheard@followcrom.com
sleep 2
tail -n 3 /var/log/mail.log        # expect: status=sent (delivered to mailbox)
ls -l /var/mail/samheard           # the mbox should now exist
: > /var/mail/samheard             # clear the throwaway test message
```

### Reading the samheard mailbox

`mail` on its own reads root's inbox; point it at the samheard mbox with `-f`:

```bash
mail -f /var/mail/samheard
```

In the prompt: a number reads that message, `d N` deletes message N, `q` quits
saving changes, `x` quits without saving. For a quick non-interactive peek:

```bash
less /var/mail/samheard
grep -i '^Subject:' /var/mail/samheard   # e.g. "Subject: heard 1008829"
```

You rarely need this — Samo consumes and deletes recognised `heard <id>`
messages on each run. Only stray messages (e.g. a test ping, whose id isn't a
real episode) will linger. Avoid reading the mbox at the exact moment the cron
job runs, since both write the file.

## Running

```bash
python samo.py
```

### As a cron job

Runs every Sunday at 22:00, logging to `samo.log`:

```cron
0 22 * * 0 cd /opt/samo && /opt/samo/samo_venv/bin/python samo.py >> /opt/samo/samo.log 2>&1
```

The app lives in `/opt/samo` (root-owned, `chmod 700`) rather than under a web
root, so its files are never exposed over HTTP.

Cron uses the VM's local time — check with `timedatectl` and set the timezone
(`timedatectl set-timezone Europe/London`) if the box defaults to UTC.
