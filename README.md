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
   SAMO_NTFY_TOKEN=tk_your_ntfy_access_token
   SAMO_NTFY_TOPIC=your-ntfy-topic
   SAMO_NTFY_EMAIL=you@example.com
   ```

   The ntfy keys are `SAMO_`-prefixed so they never collide with the `NTFY_*`
   variables the ntfy CLI injects when running Samo as a trigger hook (see
   *Triggering a run on demand*).

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

- **Correct mbox ownership** — Postfix's `local` agent delivers **as the
  `samheard` user**, so `/var/mail/samheard` must be owned by `samheard` (group
  `mail`). If it's owned by anyone else, delivery bounces with
  *"cannot update mailbox /var/mail/samheard ... Permission denied"* and every
  mark-heard mail is lost. The classic way to get into this state is creating or
  truncating the file as root **before it exists** (e.g. `: > /var/mail/samheard`),
  which leaves it `root:mail`. Fix it with:

  ```bash
  chown samheard:mail /var/mail/samheard
  chmod 660 /var/mail/samheard
  ```

  Because the cron runs as **root**, the `mailbox` rewrite during ingestion would
  normally flip the file back to `root:root`; `samo.py` guards against this by
  restoring the pre-run owner/mode after it removes messages.

Test that delivery works:

```bash
printf 'Subject: heard testping\n\ntest\n' | sendmail -i samheard@followcrom.com
sleep 2
tail -n 3 /var/log/mail.log        # expect: status=sent (delivered to mailbox)
ls -l /var/mail/samheard           # the mbox should now exist, owned samheard:mail
truncate -s0 /var/mail/samheard    # clear the throwaway test message (preserves owner)
```

> Use `truncate -s0` (not `: >`) to clear the mbox: it empties the existing file
> in place and keeps its ownership. `: >` is only dangerous when the file doesn't
> yet exist, since the redirect then *creates* it as root.

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

Two runs can't clobber each other: on startup `samo.py` takes a non-blocking
lock on `.samo.lock`, so if a second instance starts while one is running (e.g.
an on-demand trigger overlapping the cron) it exits cleanly instead of racing on
`heard.json` and the mbox.

### Triggering a run on demand

The weekly cron is the baseline, but you often hear about a new episode sooner
(Sam's mailing list, etc.) and don't want to wait until Sunday. Samo can be
poked to run immediately via [ntfy](https://ntfy.sh) — the same service it
already uses to notify you, just in the other direction.

The idea: the box **subscribes** to a private ntfy topic and runs `samo.py`
whenever a message arrives on it. You trigger a run by publishing to that topic
(tap send in the ntfy app, or `curl`), from wherever you are.

1. **Pick a private topic** — a long, random name, separate from `NTFY_TOPIC`.
   On free ntfy.sh the topic name is the only secret, so treat it like a
   password and keep it out of git:

   ```bash
   echo "samrun-$(openssl rand -hex 8)"
   ```

   Subscribe to that topic in the ntfy phone app so you can publish to it.

2. **Install the ntfy CLI on the box** — the script talks to ntfy over plain
   HTTP, so the box doesn't have the `ntfy` program yet. Either add the
   [apt repo](https://docs.ntfy.sh/install/) or drop in the single binary from
   the [releases](https://github.com/binwiederhier/ntfy/releases) under
   `/usr/local/bin/ntfy`. Confirm with `ntfy --version`.

3. **Install the systemd service** — copy `samo-trigger.service` to
   `/etc/systemd/system/`, replacing `samrun-REPLACE_WITH_YOUR_TOPIC` with your
   topic and checking the `ntfy` path matches `which ntfy`:

   ```bash
   cp samo-trigger.service /etc/systemd/system/
   # edit the topic + path, then:
   systemctl daemon-reload
   systemctl enable --now samo-trigger.service
   systemctl status samo-trigger.service        # expect: active (running)
   ```

   It runs as **root** (Samo needs `/opt/samo` and the mailbox) and restarts
   itself on crash, network blips, or reboot.

   > **Keep the trigger topic distinct from `SAMO_NTFY_TOPIC`.** When ntfy runs
   > the command it injects the triggering message's topic as `$NTFY_TOPIC`.
   > Samo's own config keys are `SAMO_`-prefixed precisely so this injected
   > variable can't be mistaken for Samo's publish topic — otherwise Samo would
   > publish into the trigger topic, which the subscriber would re-trigger,
   > looping until ntfy returns `429 Too Many Requests`. Still, give the two
   > topics different names so a run never re-triggers itself.

4. **Trigger it** — publish to the topic from the ntfy app (or
   `curl -d "go" https://ntfy.sh/samrun-<your-topic>`). Watch it with
   `journalctl -u samo-trigger.service -f`. With nothing new in the feed you'll
   get the *"All quiet from Sam"* heartbeat, confirming the chain works.
