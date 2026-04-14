# wiki

Password-protected web frontend for the Obsidian vault at `~/Vault/Learn`,
produced by the `nkr` learn agent. Runs locally on macOS and is exposed to
the internet via a Cloudflare Tunnel — no VPS, no container, no content
git repo. The app reads the vault directly; any edit in Obsidian shows up
on the next page load.

## Stack

Python 3.11+ · FastAPI · Jinja2 · python-markdown · itsdangerous.

## Layout

```
app/
  main.py            routes, lifespan, auth dependency
  auth.py            single-password session cookie
  content.py         vault loader → in-memory Index
  markdown_render.py python-markdown with a [[wikilink]] extension
  slugs.py           filename/title slug helpers
  sync.py            mtime-based vault reload
  templates/         Jinja templates
static/
  style.css          light+dark themed typography
deploy/
  com.harshdeep.wiki.plist.example   LaunchAgent template (copy, fill in, gitignored)
  cloudflared-config.example.yml     Cloudflare Tunnel config template
tests/
  test_smoke.py      index, wikilink resolver, slugify
```

## Dev

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# .env (gitignored)
cat > .env <<'EOF'
WIKI_PASSWORD=dev
SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
CONTENT_DIR=$HOME/Vault/Learn
COOKIE_SECURE=0
EOF

set -a; source .env; set +a
PYTHONPATH=. uvicorn app.main:app --reload --port 8765
```

Open http://localhost:8765 and log in. Editing a note in Obsidian and
refreshing the browser shows the change — the vault is re-read only when
its max mtime advances.

## Tests

```sh
PYTHONPATH=. .venv/bin/python tests/test_smoke.py
```

## Running in the background (macOS LaunchAgent)

1. Copy the template (it's gitignored at `deploy/com.harshdeep.wiki.plist`):

   ```sh
   cp deploy/com.harshdeep.wiki.plist.example deploy/com.harshdeep.wiki.plist
   ```

2. Generate a real `SECRET_KEY`:

   ```sh
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

3. Edit `deploy/com.harshdeep.wiki.plist` and replace the two `REPLACE_ME`
   values with your password and the generated secret.
4. Install it into the user LaunchAgents dir and load it:

   ```sh
   cp deploy/com.harshdeep.wiki.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.harshdeep.wiki.plist
   ```

5. Verify:

   ```sh
   launchctl list | grep com.harshdeep.wiki
   curl -s http://127.0.0.1:8765/healthz
   tail ~/Library/Logs/wiki.err.log
   ```

6. To reload after changes:

   ```sh
   launchctl unload ~/Library/LaunchAgents/com.harshdeep.wiki.plist
   launchctl load   ~/Library/LaunchAgents/com.harshdeep.wiki.plist
   ```

The LaunchAgent keeps the app on port `127.0.0.1:8765` — never exposed to
the LAN. The only external access path is the Cloudflare Tunnel below.

## Exposing to the internet via Cloudflare Tunnel

Prerequisites: a domain on Cloudflare (any plan, including free).

### 1. Install cloudflared

```sh
brew install cloudflared
```

### 2. Authenticate to your Cloudflare account

```sh
cloudflared tunnel login
```

A browser opens; pick your domain and authorize. This drops a cert at
`~/.cloudflared/cert.pem`.

### 3. Create a tunnel

```sh
cloudflared tunnel create wiki
```

It prints a tunnel UUID and writes credentials to
`~/.cloudflared/<UUID>.json`. Save that UUID for the next step.

### 4. Route a DNS hostname at the tunnel

Pick a subdomain (e.g. `wiki.yourdomain.com`):

```sh
cloudflared tunnel route dns wiki wiki.yourdomain.com
```

Cloudflare creates a proxied CNAME record pointing at the tunnel.

### 5. Write the tunnel config

```sh
mkdir -p ~/.cloudflared
cp deploy/cloudflared-config.example.yml ~/.cloudflared/config.yml
```

Edit `~/.cloudflared/config.yml` and:

- replace `<TUNNEL_ID>` with the UUID from step 3
- replace `wiki.example.com` with the hostname from step 4

### 6. Test it in the foreground

```sh
cloudflared tunnel run wiki
```

Open `https://wiki.yourdomain.com` in a browser. You should see the login
page served over HTTPS with a valid Cloudflare cert. Ctrl-C when happy.

### 7. Install cloudflared as a launchd service

```sh
sudo cloudflared service install
```

This registers `com.cloudflare.cloudflared` as a launchd daemon that runs
at boot, reads `~/.cloudflared/config.yml`, and keeps the tunnel up.
Verify:

```sh
sudo launchctl list | grep cloudflared
```

You should see `com.cloudflare.cloudflared` with PID.

### 8. Verify end-to-end

```sh
curl -sS https://wiki.yourdomain.com/healthz
```

Should return `{"ok":true,"loaded":true}`. Now browse to
`https://wiki.yourdomain.com`, log in, and confirm the homepage lists your
topics.

## Operating notes

- **No redeploy needed** for content. Edit a note in Obsidian; the next
  request hits a fresh index. The scan is a few milliseconds at 88 files.
- **No redeploy needed** for code changes either — just `launchctl unload`
  + `launchctl load` the wiki plist.
- **Logs**: `~/Library/Logs/wiki.{out,err}.log` for the app,
  `/Library/Logs/com.cloudflare.cloudflared.{out,err}.log` for the tunnel.
- **Locking down further**: if you don't want the password login at all,
  put Cloudflare Access in front of the hostname (Zero Trust → Access →
  Applications) and let Cloudflare handle auth; `WIKI_PASSWORD` still
  defends the origin but becomes secondary.
- **Machine asleep = site down**. macOS will let the tunnel service run
  with the lid open; if you close the lid the machine sleeps and the
  tunnel drops. For always-on access, `caffeinate -i` or Energy Saver
  "prevent automatic sleeping when display is off".
