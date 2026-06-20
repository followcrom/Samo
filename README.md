# Samo

<div align="center">
  <img src="https://github.com/followcrom/Samo/blob/main/imgs/making_sense_blue-1024x1024.png" alt="Making Sense Icon" width="225" height="225" />
</div>

## 📅 Commit Activity 🕹️

![GitHub last commit](https://img.shields.io/github/last-commit/followcrom/Samo)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/followcrom/Samo)
![GitHub repo size](https://img.shields.io/github/repo-size/followcrom/Samo)

Polls the podcast RSS feed and sends an ntfy push notification for each new
episode. Runs unattended as a weekly cron job, and you can poke it on demand —
check for new episodes, or recall recent ones — straight from the ntfy app.

## How it works

1. Reads the feed and extracts each episode's audio enclosure.
2. Pushes a notification per **un-notified** episode (title + download link) and
   records those IDs in `notified`.
3. If there are no un-notified episodes, it sends a single **"All quiet from
   Sam"** heartbeat instead, including the most recent episode title so you can
   see the feed is being read.

Each episode is notified exactly once. To deliberately re-surface a recent
episode — e.g. a push you missed or dismissed — use **Recall** (see *Using Samo
from your phone*).

The filename `heard.json` is historical — it once also tracked
a `heard` set; that's gone, and an older `{"notified": [...], "heard": [...]}`
file is read fine, with the `heard` key ignored.

## Using Samo from your phone

Day to day you don't touch the server. You drive Samo by publishing a short
message to your private `samrun-…` trigger topic from the **ntfy app** — open
that topic, type one of the commands below as the message, and send. (This works
once the on-demand trigger is deployed; see *Deployment*.)

| Send this              | What Samo does                                                        |
| ---------------------- | -------------------------------------------------------------------- |
| `go` (or any text)     | **Run once now**: check the feed and push any *new* episodes.        |
| `recall`               | List the **3** most recent episodes to browse.                       |
| `recall 5`             | List the most recent **5**. `recent` is a synonym; any count works.  |

**Run once.** A check pushes one notification per new episode. If nothing is new
you get the **"All quiet from Sam"** heartbeat, so you always get confirmation
the run happened.

**Recall.** The normal flow notifies each episode only once, so a push you
missed or dismissed is otherwise gone — recall re-surfaces it. Each recall item
is a **title only**; tap the notification to open/download that episode. Recall
never changes state, so run it as often as you like.

> The same commands work from anywhere with `curl`, e.g.
> `curl -d "recall 5" https://ntfy.sh/samrun-<your-topic>`. Under the hood ntfy
> hands your message to the script as `$NTFY_MESSAGE`: `recall`/`recent` (with an
> optional count) means recall; anything else is a normal check.

## Running on the box

For development, or a manual run on the server itself, the same actions are plain
command-line flags:

```bash
python samo.py             # run once (what the cron and a plain trigger do)
python samo.py --recent    # recall the last 3
python samo.py --recent 5  # recall the last 5
```

These are identical to the phone commands — the app just delivers the same
instruction remotely.

## Deployment

Samo lives in `/opt/samo` (root-owned, `chmod 700`) rather than under a web root,
so its files are never exposed over HTTP.

### Weekly cron

Runs every Sunday at 22:00, logging to `samo.log`:

```cron
0 22 * * 0 cd /opt/samo && /opt/samo/samo_venv/bin/python samo.py >> /opt/samo/samo.log 2>&1
```

Cron uses the VM's local time — check with `timedatectl` and set the timezone
(`timedatectl set-timezone Europe/London`) if the box defaults to UTC.

Two **normal** runs can't clobber each other: before touching `heard.json`,
`samo.py` takes a non-blocking lock on `.samo.lock`, so a second normal run (e.g.
a trigger overlapping the cron) exits cleanly instead of racing on the state
file. Recall runs skip the lock — they never write state, so they always proceed.

### On-demand triggers

This is what lets you control Samo from your phone (see *Using Samo from your
phone*). The box **subscribes** to a private ntfy topic and runs `samo.py`
whenever a message lands on it, passing the message body through as the command —
so publishing to that topic from the app (or `curl`) triggers a run from
wherever you are.

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

   It runs as **root** (Samo lives in root-owned `/opt/samo`) and restarts
   itself on crash, network blips, or reboot.

   > **Keep the trigger topic distinct from `SAMO_NTFY_TOPIC`.** When ntfy runs
   > the command it injects the triggering message's topic as `$NTFY_TOPIC`.
   > Samo's own config keys are `SAMO_`-prefixed precisely so this injected
   > variable can't be mistaken for Samo's publish topic — otherwise Samo would
   > publish into the trigger topic, which the subscriber would re-trigger,
   > looping until ntfy returns `429 Too Many Requests`. Still, give the two
   > topics different names so a run never re-triggers itself.

4. **Test it** — publish `go` to the topic from the ntfy app (or
   `curl -d "go" https://ntfy.sh/samrun-<your-topic>`). Watch it with
   `journalctl -u samo-trigger.service -f`. With nothing new in the feed you'll
   get the *"All quiet from Sam"* heartbeat, confirming the chain works — then
   try `recall` to see the browse list.
