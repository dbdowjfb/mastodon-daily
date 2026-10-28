#!/usr/bin/env bash
# Netlify build: generate the Mastodon daily feed into public/.
# This is the "all-Netlify" replacement for .github/workflows/daily.yml.
# The scheduled function netlify/functions/daily-cron.mjs triggers this build.
set -euo pipefail

echo "=== netlify build: mastodon-daily ==="
python3 --version

# Use a venv so we never hit a PEP 668 "externally-managed environment" error.
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip wheel
# CPU-only torch keeps the download/install small on the build VM.
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[embed,test]"

echo "=== tests ==="
python -m pytest -q

echo "=== build feed ==="
mkdir -p public
# Fresh data each run (no persistence): drop any committed history.
rm -f public/items.json public/feed.xml public/index.html

# Use Netlify's own site URL for the RSS <link> unless FEED_LINK is set explicitly.
export FEED_LINK="${FEED_LINK:-${URL:-https://mastodon-daily.netlify.app}}"

python -m mastodon_daily.main

echo "=== done; public/ ==="
ls -la public
