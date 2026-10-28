# AGENTS.md

This repo builds a daily Mastodon recommendation RSS feed: fetch favourites/bookmarks/boosts as interest signals, pull candidates from home + public(local) + public(global), rank via local text embeddings (images captioned to text by an LLM), emit `public/feed.xml` and `public/index.html`, then publish via a Netlify **Deploy Preview** (the GitHub Action pushes `public/` to a `feed` branch). The daily trigger is the `hw` VPS (GitHub cron is unreliable).

## Key files
- `mastodon_daily/main.py` — entrypoint (`python -m mastodon_daily.main`). Loads `config.yaml` with `${ENV,default}` substitution.
- `mastodon_daily/masto.py` — minimal Mastodon API client. Normalizes statuses (unwraps reblogs), strips HTML, collects media preview URLs, paginates via `Link` header.
- `mastodon_daily/ranker.py` — ranking + translation. `rank()` tries the **local embedding reranker** (`_rank_embeddings`: profile embedded as documents, recency-weighted cosine vs candidates) and falls back to the keyword+media heuristic (`_rank_heuristic`). Embeddings run locally via `sentence-transformers` (no API); returns `None` on any failure → heuristic. Images are **not** embedded (CLIP disabled): `resolve_caption_cfg` + `caption_items` ask an OpenAI-compatible vision model (defaults to the `llm` endpoint) for a short alt text per image and store it under `_alt`; `_candidate_text` folds text+tags+`_alt`, so the text embedding covers images too. Also translation: `needs_translation` (main.py) picks the non-Chinese posts, and `translate_to_zh` batches them to DeepSeek (chunks of 16), retries 429/5xx with backoff, and **splits a failing chunk in half** (≤4 levels) before giving up; it logs `translate: n/total succeeded`. DeepSeek JSON chat ranking was abandoned (returns empty `content`, text in `reasoning_content`) — keep any LLM JSON parsing tolerant (first `{ ... }`).
- `mastodon_daily/feed.py` — merges `public/items.json` (history), dedupes by `guid`, sorts by pubDate desc, writes RSS + index.
- `config.yaml` — all tunables (limits, window_hours, max_candidates, rank.top_n, `reranker` block, `caption` block, LLM params). Defaults: DeepSeek `deepseek-flash` (`llm.*`), reranker `jinaai/jina-embeddings-v5-text-nano` with `encode_kwargs: {task: retrieval, prompt_name: document}`, `caption` (image→alt-text; empty `base_url`/`api_key`/`model` fall back to `llm.*`), `reranker.image.enabled: false` (CLIP dropped).
- `.github/workflows/daily.yml` — the build+publish path. `on: workflow_dispatch` only (no cron; GitHub's is unreliable — `hw` triggers it). Installs CPU-only torch + `.[embed,test]`, runs pytest, builds **fresh** (`rm -f public/items.json …`), runs `python -m mastodon_daily.main`, then force-pushes `public/` to the **`feed` branch** → Netlify Deploy Preview. `FEED_LINK` is the Deploy Preview URL.
- `netlify.toml` — `publish = "public"`; `[context.deploy-preview]`/`[context.branch-deploy]` set the command to a no-op (the `feed` branch already carries built `public/`, so Netlify only serves it); sets `Content-Type: application/rss+xml` for `/feed.xml`.
- `scripts/netlify_build.sh` — alternative build (venv → CPU torch → pytest → fresh data → main) used by the earlier "build on Netlify" path; not used by the current GH-Action path. Kept for local/Netlify builds.
- `netlify/functions/daily-cron.mjs` — legacy Netlify scheduled function that POSTs `BUILD_HOOK_URL`. Not part of the current chain (it only triggers skipped production builds while the team is credit-paused); remove when redeploying to Netlify.
- `tests/` — offline pytest suite: translation (chunk/retry/parse-failure), ranking (fake embeddings + heuristic fallback), captioning (fake HTTP), feed item link/translation, config `${VAR,default}` substitution (empty env → default).
- `public/` — generated artifacts (`feed.xml`, `index.html`, `items.json`, `media/`). Rebuilt fresh by the GH Action and force-pushed to the `feed` branch each run; the copies on `main` are just a snapshot.

## Commands
- Local run: `mkdir -p public && python -m mastodon_daily.main` (requires env vars below). Run from repo root with `PYTHONPATH` set if needed, or `pip install -e .`.
- Install in dev: `pip install -e .` (or `uv pip install -e .`); embeddings need `pip install -e ".[embed]"`.
- Tests: `python -m pytest -q` (install `pip install -e ".[test]"` first). Tests are offline — HTTP is monkeypatched and `sentence_transformers` is faked, so no model download.

## Environment variables (required for real run)
- `MASTODON_BASE_URL` (default `https://mas.to`)
- `MASTODON_TOKEN` (Mastodon access token)
- `MASTODON_ACCOUNT_ID` (optional; if empty uses `verify_credentials`)
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`)
- `DEEPSEEK_MODEL` (default `deepseek-flash`)
- `FEED_LINK` (e.g. Netlify site URL used in RSS channel; build script defaults it to Netlify's `$URL`)
- `CAPTION_BASE_URL` / `CAPTION_API_KEY` / `CAPTION_MODEL` (optional; default to the `DEEPSEEK_*` values — the caption model must accept image input)

## Execution flow
1. `collect_signals` pulls favourites (limit), bookmarks (limit), and this account's boosts (`/api/v1/accounts/:id/statuses?exclude_replies=true`) → dedupe by id → sort newest first → `profile = signals[:profile.max_items]`.
2. `collect_candidates` pulls home, public(local=true), public(local=false) with limits → normalize, skip engaged IDs, skip older than `window_hours`, skip empty (no text+no media), dedupe → cap `max_candidates`.
3. If `caption.enabled`, `caption_items` asks a vision model for a short alt text per image and stores it under `_alt` (all failures swallowed → ranking still works on text). `rank` then uses the local text-embedding reranker when `reranker.enabled` (top `rank.top_n`); media-only/leftover slots are filled as `多样性补位`. Falls back to the keyword heuristic if `sentence-transformers` is missing or the model fails. Any LLM JSON parsing should tolerate `reasoning_content` with empty `content` (extract first `{ ... }`).
4. Build items, merge with `public/items.json` (dedupe by guid, keep newest first, trim to `feed.max_items_kept`), write `feed.xml` + `index.html` + `items.json`.

## Conventions / gotchas
- Normalization unwraps `reblog` to original post (recommend the original). Media uses `preview_url` if present, else `url`. HTML stripped.
- Candidate dedupe includes `engaged` set (signals) to avoid re-recommending what you already interacted with.
- `items.json` is the history file committed to repo (acts as dedupe + rolling window). Don’t delete it unless intentionally resetting.
- Ranker: embeddings are computed locally in CI (free); the jina v5 model requires `peft` and `task=retrieval`. If the model/deps are unavailable `_rank_embeddings` returns `None` and the heuristic runs — the feed never fails for this reason.
- DeepSeek chat completions: `response_format: {"type":"json_object"}` requires "json" in prompt; some models return reasoning separately, so parse the first `{ ... }` from `content` **or** `reasoning_content`. `translate_to_zh` retries 429/5xx, splits a failing batch in half, logs `translate: n/total succeeded`, and returns untranslatable text as-is. `needs_translation` (main.py) skips Chinese (language `zh*`, or Han without kana/hangul) — edge cases (kanji-only Japanese, mislabelled `und`/language) are known and untested.
- RSS channel uses `FEED_LINK` for `<link>`; feed items include media thumbnails in description (HTML). `netlify.toml` forces RSS content-type.
- Action commits with bot identity and deploys to Netlify using `NETLIFY_AUTH_TOKEN` + `NETLIFY_SITE_ID`. Workflow uses `fetch-depth: 0` (not strictly required but fine).
- Netlify team SSO can redirect unauthenticated requests; use the deploy permalink or published alias as `FEED_LINK` for RSS readers (the current setup points to the site alias after disabling SSO).
- Config uses `${VAR,default}` substitution in `load_config` (comma-separated default). Empty account_id falls back to verify_credentials.
- Keep changes minimal and repo-specific; avoid generic advice. Trust executable sources (config/workflows/code) over docs when conflicting.

## Scheduling & deployment (current, verified)
Chain: **hw VPS cron → GitHub Actions (compute) → push `public/` to `feed` branch → Netlify Deploy Preview**.
- **Timer**: on `hw` (see below), root crontab `0 18 * * *` — hw is US/Eastern, so 18:00 EDT = 22:00 UTC = 06:00 +08. Runs `/root/mastodon-daily-trigger.sh`, which POSTs the GitHub API `workflow_dispatch` for `daily.yml` (PAT at `/root/.config/mastodon-daily/gh_token`, scope includes `workflow`). GitHub's own cron is unreliable, hence hw. **DST caveat**: when US/Eastern switches to EST the same cron fires at 23:00 UTC = 07:00 +08; for exact year-round 06:00 +08, set hw's TZ to UTC (`timedatectl set-timezone UTC`) and use `0 22 * * *`.
- **Compute**: `.github/workflows/daily.yml` (ubuntu runner) — CPU torch + `.[embed,test]`, pytest, build fresh, force-push `public/` to `feed`.
- **Publish**: the **open PR `feed` → `main`** (#1) makes Netlify build a **Deploy Preview** on every push — free/unlimited, unlike production deploys (15 credits each; the Free team is currently **credit-paused**). Feed URL = **https://deploy-preview-1--mastodon-daily-feed.netlify.app/feed.xml** (this is `FEED_LINK`, in `config.yaml` and hardcoded in the workflow).
- Netlify site `mastodon-daily-feed` (id `d13e7258-52b0-43b2-b040-6b1f6ac3cf03`), Git-linked (GitHub App, prod branch `main`); site env vars: `MASTODON_*`, `DEEPSEEK_API_KEY`, `FEED_LINK`, `BUILD_HOOK_URL`.
- **Gotchas**: (a) don't close/merge PR #1 — the feed URL depends on it (for a PR-independent URL, enable **Branch deploys → add `feed`** in the Netlify UI and switch `FEED_LINK` to `https://feed--mastodon-daily-feed.netlify.app`). (b) Production deploys on `main` are skipped (credits) — pushes to `main` are only for code. (c) Don't re-add a `/feed.xml → /feed.xml` `force = true` redirect (404s).

## History
- GitHub cron (`schedule: cron '0 22 * * *'`) proved unreliable (3–5 h late, skipped slots) → replaced by the `hw` trigger.
- Netlify production deploys cost 15 credits each (Free = 300/mo) → the team got credit-paused → publishing moved to a free Deploy Preview (branch deploy / deploy previews are unlimited).
- `scripts/netlify_build.sh`, `netlify.toml`'s build command, and `netlify/functions/daily-cron.mjs` are leftovers from the earlier "build on Netlify" design.

## hw server (self-hosted VPS, reachable via `ssh hw`)
- Hostwinds VPS, Debian 12, kernel 6.1, x86_64. **1 vCPU** (Xeon E5-2670 v2), **960 MB RAM with no swap**, 30 GB disk (22 GB free), idle (load ~0).
- Tools: `python3.11` (no `pip`), `docker`, `git`; **passwordless sudo** available. Network OK to GitHub (200), mas.to (200), DeepSeek (401 = reachable), rss.arxiv.org (200), api.netlify.com (reachable). Also runs the `ntfy` service.
- Fitness:
  - **As a scheduler/trigger only** (curl GitHub API): plenty — negligible CPU/RAM. Recommended.
  - **As a full runner** (torch + jina-v5 + CLIP): RAM is the limit. 960 MB with no swap is tight for loading both embedding models; would need a swapfile (`sudo`) + running the two models in separate processes, and Netlify deploy would have to switch to the Netlify API/CLI (the GH Action does it today). Feasible but heavier/more fragile than triggering GitHub.
