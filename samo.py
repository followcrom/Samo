import feedparser, requests, os, json, sys, re, mailbox, fcntl

STATE_FILE = os.path.join(os.path.dirname(__file__), 'heard.json')

ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)

# The above allows us to set env vars in a .env file, but they can also be set in the environment directly. This is useful for CI or if you don't want to store secrets in a file.

FEED_URL = os.environ.get('FEED_URL')
if not FEED_URL:
    sys.exit('FEED_URL not set (env var or .env)')

# Config keys are SAMO_-prefixed so they never collide with the NTFY_* variables
# the ntfy CLI injects into our environment when it runs us as a subscribe hook.
# Without the prefix, ntfy's injected NTFY_TOPIC (the trigger topic) would win
# over .env here and Samo would publish into its own trigger topic — a loop.
NTFY_TOKEN = os.environ.get('SAMO_NTFY_TOKEN')
if not NTFY_TOKEN:
    sys.exit('SAMO_NTFY_TOKEN not set (env var or .env)')

NTFY_TOPIC = os.environ.get('SAMO_NTFY_TOPIC')
if not NTFY_TOPIC:
    sys.exit('SAMO_NTFY_TOPIC not set (env var or .env)')
NTFY_URL = f'https://ntfy.sh/{NTFY_TOPIC}'

NTFY_EMAIL = os.environ.get('SAMO_NTFY_EMAIL')
if not NTFY_EMAIL:
    sys.exit('SAMO_NTFY_EMAIL not set (env var or .env)')

# Address you email to mark an episode heard, and the local mbox where Postfix
# delivers it. Both default to the dedicated samheard mailbox on the VM.
HEARD_ADDR = os.environ.get('HEARD_ADDR', 'samheard@followcrom.com')
HEARD_MAILBOX = os.environ.get('HEARD_MAILBOX', '/var/mail/samheard')

# Subject of a mark-heard email looks like "heard 1008829"; grab the id.
HEARD_SUBJECT_RE = re.compile(r'\bheard\s+(\S+)', re.I)


def load_state():
    """Return {'notified': set, 'heard': set}, or None on first run.

    Migrates the old flat-list format (a bare JSON array of ids) by treating
    every id as already notified *and* heard — that list was the baseline of
    episodes we'd already dealt with, so it shouldn't resurface as a backlog.
    """
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        data = json.load(f)
    if isinstance(data, list):
        return {'notified': set(data), 'heard': set(data)}
    return {'notified': set(data.get('notified', [])),
            'heard': set(data.get('heard', []))}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump({'notified': sorted(state['notified']),
                   'heard': sorted(state['heard'])}, f, indent=2)


def collect_marked_heard(known_ids):
    """Scan the local mbox for 'heard <id>' emails, returning the set of ids
    found (restricted to known_ids) and deleting those messages. Never raises:
    a mailbox problem must not stop notifications going out."""
    marked = set()
    if not os.path.exists(HEARD_MAILBOX):
        return marked
    try:
        st = os.stat(HEARD_MAILBOX)
        box = mailbox.mbox(HEARD_MAILBOX)
        box.lock()
        try:
            for key, msg in list(box.iteritems()):
                m = HEARD_SUBJECT_RE.search(str(msg.get('subject', '')))
                if m and m.group(1) in known_ids:
                    marked.add(m.group(1))
                    box.remove(key)
            box.flush()
        finally:
            box.unlock()
            box.close()
        # When we remove messages, mailbox.flush() rewrites the spool via a
        # temp file + rename, so the file inherits *this* process's owner. Run
        # from root's cron that turns it into root:root, after which Postfix
        # (which delivers as the samheard user) can no longer write it and all
        # future mark-heard mail bounces. Restore the pre-run owner/mode.
        if marked:
            try:
                os.chown(HEARD_MAILBOX, st.st_uid, st.st_gid)
                os.chmod(HEARD_MAILBOX, st.st_mode & 0o777)
            except OSError as e:
                print(f'could not restore ownership of {HEARD_MAILBOX}: {e}',
                      file=sys.stderr)
    except Exception as e:
        print(f'could not read mailbox {HEARD_MAILBOX}: {e}', file=sys.stderr)
    return marked


# Single-instance lock: the weekly cron and a mail-triggered run could fire at
# once, and two instances would race on heard.json and the mbox. Take a
# non-blocking lock; if another run holds it, bow out quietly. _lock_fh is kept
# open for the process lifetime so the lock is held until we exit.
LOCK_FILE = os.path.join(os.path.dirname(__file__), '.samo.lock')
_lock_fh = open(LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit('another samo run is in progress; exiting')

feed = feedparser.parse(FEED_URL)

# Build episode list: (id, title, url)
episodes = []
for entry in feed.entries:
    for enc in entry.enclosures:
        if enc.type == 'audio/mpeg':
            episodes.append((entry.id, entry.title, enc.href))
            break

# Load state, or seed it on first run
state = load_state()
if state is None:
    print(f'No {os.path.basename(STATE_FILE)} found. Feed currently has {len(episodes)} episodes.')
    sys.exit(0)

notified = state['notified']
heard = state['heard']

# 1. Ingest any "mark heard" emails that arrived since the last run.
newly_heard = collect_marked_heard(notified)
if newly_heard:
    heard |= newly_heard
    print(f'Marked {len(newly_heard)} episode(s) heard from email.')

# 2. Split and display
pending = [(eid, title, url) for eid, title, url in episodes if eid not in heard]
unnotified = [(eid, title, url) for eid, title, url in episodes if eid not in notified]

print(f'\nTotal episodes of Making Sense: {len(episodes)}')
if episodes:
    print(f'\nMost recent episode: {episodes[0][1]}')
print(f'\nYou have listened to {len(episodes) - len(pending)} episodes.')

# 3. Notify about episodes we have not pushed yet.
if unnotified:
    print(f'\nThere are {len(unnotified)} new episodes to notify:')
    for _, title, _ in unnotified:
        print(f'  {title}')

    for eid, title, url in unnotified:
        mark_heard = f'mailto:{HEARD_ADDR}?subject=heard%20{eid}'
        body = (f"{title}\n\nDownload:\n{url}\n\n"
                f"Mark heard (tap the button in the app, or this link by email):\n{mark_heard}")
        try:
            r = requests.post(
                NTFY_URL,
                data=body.encode('utf-8'),
                headers={
                    'Title': title,
                    'Tags': 'headphones',
                    'Click': url,
                    'Actions': f'view, "Mark heard", {mark_heard}, clear=true',
                    'Email': NTFY_EMAIL,
                    'Authorization': f'Bearer {NTFY_TOKEN}',
                },
                timeout=15,
            )
            r.raise_for_status()
            notified.add(eid)
        except requests.RequestException as e:
            print(f'ntfy ping failed for {title!r}: {e}', file=sys.stderr)
else:
    recent = f'\n\nMost recent episode: {episodes[0][1]}' if episodes else ''
    body = f'All quiet from Sam — {len(episodes) - len(pending)} episodes heard.{recent}'
    try:
        r = requests.post(
            NTFY_URL,
            data=body.encode('utf-8'),
            headers={
                'Title': 'All quiet from Sam',
                'Tags': 'zzz',
                'Priority': '1',
                'Email': NTFY_EMAIL,
                'Authorization': f'Bearer {NTFY_TOKEN}',
            },
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f'ntfy heartbeat failed: {e}', file=sys.stderr)

save_state(state)
