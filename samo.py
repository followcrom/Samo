import feedparser, requests, os, json, sys, fcntl

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

# How many recent episodes "recall" lists when no count is given.
RECALL_DEFAULT_N = 3


def load_state():
    """Return the set of episode ids we've already pushed a notification for,
    or None on first run.

    Accepts older on-disk formats: a bare JSON array (the original baseline
    list of dealt-with ids) and the legacy {'notified': [...], 'heard': [...]}
    dict — in which case the dropped 'heard' key is simply ignored.
    """
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        data = json.load(f)
    if isinstance(data, list):
        return set(data)
    return set(data.get('notified', []))


def save_state(notified):
    with open(STATE_FILE, 'w') as f:
        json.dump({'notified': sorted(notified)}, f, indent=2)


def parse_mode():
    """Decide what kind of run this is, returning one of:
      ('normal', None) — the weekly cron / a plain trigger; notify new episodes
      ('recall', n)    — list the N most recent episodes (each with a Download
                         button that opens the audio directly)

    The verb is read from CLI args first, else from the trigger message body
    ($NTFY_MESSAGE, which the ntfy CLI sets). A leading '--' is tolerated, so
    `--recent 5`, `recent 5`, and `recall` all work. Anything else (a body like
    'go', or no args at all) is a normal run. N defaults to RECALL_DEFAULT_N.
    """
    toks = sys.argv[1:] or os.environ.get('NTFY_MESSAGE', '').strip().split()
    if not toks:
        return 'normal', None
    verb, rest = toks[0].lstrip('-').lower(), toks[1:]
    if verb in ('recent', 'recall'):
        n = RECALL_DEFAULT_N
        if rest:
            try:
                n = max(1, int(rest[0]))
            except ValueError:
                pass
        return 'recall', n
    return 'normal', None


def dephone(text):
    """Insert an invisible word joiner between adjacent digits so the phone's
    notification auto-linkifier stops turning numbers (episode #s, years, the
    feed count) into tappable 'phone numbers'. Visually identical. Use only on
    plain text — never on a URL, whose digits would be corrupted — and only in
    the message body, since the (non-latin-1) joiner can't go in an HTTP header."""
    out = []
    for i, ch in enumerate(text):
        if i and ch.isdigit() and text[i - 1].isdigit():
            out.append('⁠')  # word joiner: invisible, non-breaking
        out.append(ch)
    return ''.join(out)


def push(title, body, extra_headers=None):
    """Send one ntfy notification. Returns True on success. extra_headers layers
    on the common Title/auth headers. `title` is the bold heading, so callers
    should NOT repeat it in `body` (ntfy would show it twice)."""
    headers = {
        'Title': title,
        'Authorization': f'Bearer {NTFY_TOKEN}',
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = requests.post(NTFY_URL, data=body.encode('utf-8'),
                          headers=headers, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f'ntfy push failed for {title!r}: {e}', file=sys.stderr)
        return False


def notify_download(title, url):
    """Push a new episode. The title is the heading; tapping the notification
    opens the audio (the Click header), and the body shows the link too."""
    return push(title, f"Download:\n{url}",
                {'Tags': 'headphones', 'Click': url})


def notify_recall(title, url):
    """Push a recall entry: the title is the heading and tapping the
    notification opens the audio directly (the Click header), so a tap needs no
    round trip back through the box."""
    return push(title, "Tap to open this episode.",
                {'Tags': 'headphones', 'Click': url})


feed = feedparser.parse(FEED_URL)

# Build episode list: (id, title, url)
episodes = []
for entry in feed.entries:
    for enc in entry.enclosures:
        if enc.type == 'audio/mpeg':
            episodes.append((entry.id, entry.title, enc.href))
            break

mode, payload = parse_mode()

# Recall: an escape hatch for when an episode slipped through — it got into
# `notified` (e.g. a missed or dismissed push), so the normal flow stays quiet.
# List the N most recent episodes (titles only, each with a Download button that
# opens the audio directly) regardless of `notified`, leaving heard.json
# untouched so it's safe to run repeatedly.
if mode == 'recall':
    targets = episodes[:payload]
    print(f'Recall: listing the {len(targets)} most recent episode(s) '
          f'with Download buttons. State unchanged.')
    sent = 0
    for eid, title, url in targets:
        if notify_recall(title, url):
            sent += 1
            print(f'  listed: {title}')
    print(f'Recall complete: {sent}/{len(targets)} listed.')
    sys.exit(0)

# Single-instance lock — only the normal run needs it, because only the normal
# run reads-then-writes heard.json. Recall exits above without taking it: it
# never touches state, so a recall trigger always proceeds (even if it lands
# while the weekly cron is running). Take a non-blocking lock; if another normal
# run holds it, exit quietly. _lock_fh stays open for the process lifetime so
# the lock is held until we exit.
LOCK_FILE = os.path.join(os.path.dirname(__file__), '.samo.lock')
_lock_fh = open(LOCK_FILE, 'w')
try:
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit('another samo run is in progress; exiting')

# Load state, or seed it on first run
notified = load_state()
if notified is None:
    print(f'No {os.path.basename(STATE_FILE)} found. Feed currently has {len(episodes)} episodes.')
    sys.exit(0)

# Episodes we have not pushed a notification for yet.
unnotified = [(eid, title, url) for eid, title, url in episodes if eid not in notified]

print(f'\nTotal episodes of Making Sense: {len(episodes)}')
if episodes:
    print(f'\nMost recent episode: {episodes[0][1]}')

# Notify about episodes we have not pushed yet.
if unnotified:
    print(f'\nThere are {len(unnotified)} new episodes to notify:')
    for _, title, _ in unnotified:
        print(f'  {title}')

    for eid, title, url in unnotified:
        if notify_download(title, url):
            notified.add(eid)
else:
    recent = f'\n\nMost recent episode: {episodes[0][1]}' if episodes else ''
    push('All quiet from Sam',
         dephone(f'{len(episodes)} episodes in the feed.{recent}'),
         {'Tags': 'zzz', 'Priority': '1'})

save_state(notified)
