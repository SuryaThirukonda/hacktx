#!/usr/bin/env bash
# Expose the backend (which serves the built dashboard) through a Cloudflare quick tunnel, so a phone anywhere can
# open the target controller at https://<random>.trycloudflare.com/drive (add ?token=... when OVERWATCH_TOKEN is set).
# Needs cloudflared: https://github.com/cloudflare/cloudflared/releases (a single binary; no account for quick tunnels).
set -euo pipefail
PORT="${1:-8000}"
BIN="$(command -v cloudflared || true)"
[ -z "$BIN" ] && [ -x "$HOME/.local/bin/cloudflared" ] && BIN="$HOME/.local/bin/cloudflared"
if [ -z "$BIN" ]; then
  echo "cloudflared not found: install it from https://github.com/cloudflare/cloudflared/releases" >&2
  exit 1
fi
echo "Tunnelling http://localhost:$PORT (the backend must be running: make backend). Open <url>/drive on the phone."
"$BIN" tunnel --url "http://localhost:$PORT" --no-autoupdate 2>&1 | while IFS= read -r line; do
  if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
    echo
    echo "==> Phone controller: ${BASH_REMATCH[1]}/drive"
    echo "==> Demo page:        ${BASH_REMATCH[1]}/"
    echo
  fi
  echo "$line"
done
