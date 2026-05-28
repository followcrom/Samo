import feedparser, requests, os, json, sys

HEARD_FILE = os.path.join(os.path.dirname(__file__), 'heard.json')

ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

FEED_URL = os.environ.get('FEED_URL')
if not FEED_URL:
    sys.exit('FEED_URL not set (env var or .env)')

NTFY_TOKEN = os.environ.get('NTFY_TOKEN')
if not NTFY_TOKEN:
    sys.exit('NTFY_TOKEN not set (env var or .env)')

NTFY_TOPIC = os.environ.get('NTFY_TOPIC')
if not NTFY_TOPIC:
    sys.exit('NTFY_TOPIC not set (env var or .env)')
NTFY_URL = f'https://ntfy.sh/{NTFY_TOPIC}'

NTFY_EMAIL = os.environ.get('NTFY_EMAIL')
if not NTFY_EMAIL:
    sys.exit('NTFY_EMAIL not set (env var or .env)')

feed = feedparser.parse(FEED_URL)

# Build episode list: (id, title, url)
episodes = []
for entry in feed.entries:
    for enc in entry.enclosures:
        if enc.type == 'audio/mpeg':
            episodes.append((entry.id, entry.title, enc.href))
            break

# Load heard set, or seed it on first run
if os.path.exists(HEARD_FILE):
    with open(HEARD_FILE) as f:
        heard = set(json.load(f))
else:
    print(f'No {os.path.basename(HEARD_FILE)} found. Feed currently has {len(episodes)} episodes.')
    sys.exit(0)

# Split and display
unheard = [(eid, title, url) for eid, title, url in episodes if eid not in heard]
heard_eps = [title for eid, title, _ in episodes if eid in heard]

print(f'\nTotal episodes of Making Sense: {len(episodes)}')

print(f'\nYou have listened to ({len(heard_eps)})')

if unheard:
    print(f'\nThere are {len(unheard)} unheard episodes:')
    for _, title, _ in unheard:
        print(f'  {title}')

    for eid, title, url in unheard:
        body = f"{title}\n\nDownload:\n  wget '{url}'"
        try:
            r = requests.post(
                NTFY_URL,
                data=body.encode('utf-8'),
                headers={
                    'Title': title,
                    'Tags': 'headphones',
                    'Click': url,
                    'Email': NTFY_EMAIL,
                    'Authorization': f'Bearer {NTFY_TOKEN}',
                },
                timeout=15,
            )
            r.raise_for_status()
            heard.add(eid)
        except requests.RequestException as e:
            print(f'ntfy ping failed for {title!r}: {e}', file=sys.stderr)

    with open(HEARD_FILE, 'w') as f:
        json.dump(sorted(heard), f, indent=2)
else:
    body = f'All caught up — {len(heard_eps)} episodes heard.'
    try:
        r = requests.post(
            NTFY_URL,
            data=body.encode('utf-8'),
            headers={
                'Title': 'No new Making Sense episodes',
                'Tags': 'zzz',
                'Priority': '1',
                'Authorization': f'Bearer {NTFY_TOKEN}',
            },
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f'ntfy heartbeat failed: {e}', file=sys.stderr)


