"""Entry point: fetch signals + candidates, rank with LLM, emit RSS."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import yaml

from . import feed, ranker
from .masto import Mastodon, mirror_media, normalize

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")
ITEMS_PATH = os.environ.get("ITEMS_PATH", "public/items.json")

_KANA_HANGUL = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
_HAN = re.compile(r"[\u4e00-\u9fff]")


def needs_translation(c: dict) -> bool:
    """True only when the post is demonstrably not Chinese."""
    lang = (c.get("language") or "").lower()
    if lang:
        return not lang.startswith("zh")
    t = c.get("text") or ""
    if _KANA_HANGUL.search(t):
        return True
    return not _HAN.search(t)


def load_config(path: str) -> dict:
    def _resolve(spec):
        name, _, default = spec.partition(",")
        val = os.environ.get(name)
        return default if val is None or val == "" else val

    def sub(v):
        if isinstance(v, str):
            return re.sub(r"\$\{([^}]+)\}", lambda m: _resolve(m.group(1)), v)
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        return v

    with open(path, encoding="utf-8") as f:
        return sub(yaml.safe_load(f))


def log(*a):
    print("[masto-daily]", *a, flush=True)


def collect_signals(m, cfg):
    s = cfg["signals"]
    log("fetching interest signals (favourites/bookmarks/boosts)...")
    favs = m.favourites(s["favourites_limit"])
    bms = m.bookmarks(s["bookmarks_limit"])
    acct_id = cfg["mastodon"].get("account_id") or m.verify_credentials()["id"]
    boosts = m.boosts(acct_id, s["boosts_limit"])
    items, seen = [], set()
    for src, lst in (("fav", favs), ("bookmark", bms), ("boost", boosts)):
        for st in lst:
            n = normalize(st, src)
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            items.append(n)
    items.sort(key=lambda x: x["created_at"] or "", reverse=True)
    log(f"  {len(favs)} fav / {len(bms)} bookmark / {len(boosts)} boost -> {len(items)} unique")
    return items, seen


def collect_candidates(m, cfg, engaged):
    c = cfg["candidates"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=c["window_hours"])
    log("fetching candidate timelines (home/local/global)...")
    sources = [
        ("home", m.home(c["home_limit"])),
        ("local", m.public(local=True, limit=c["local_limit"])),
        ("global", m.public(local=False, limit=c["global_limit"])),
    ]
    out, seen = [], set()
    for src, lst in sources:
        kept = 0
        for st in lst:
            n = normalize(st, src)
            if not n["id"] or n["id"] in seen or n["id"] in engaged:
                continue
            try:
                if datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")) < cutoff:
                    continue
            except Exception:
                pass
            if not n["text"] and not n["media"]:
                continue
            seen.add(n["id"])
            out.append(n)
            kept += 1
        log(f"  {src}: {len(lst)} raw -> {kept} kept")
    out = out[: c["max_candidates"]]
    log(f"  total candidates: {len(out)}")
    return out


def main():
    cfg = load_config(CONFIG_PATH)
    m = Mastodon(cfg["mastodon"]["base_url"], cfg["mastodon"]["access_token"])

    signals, engaged = collect_signals(m, cfg)
    profile = signals[: cfg["profile"]["max_items"]]
    log(f"profile items: {len(profile)}")

    candidates = collect_candidates(m, cfg, engaged)
    if not candidates:
        log("no candidates; nothing to do")
        return

    top_n = min(cfg["rank"]["top_n"], len(candidates))

    cap_cfg = ranker.resolve_caption_cfg(cfg)
    if cap_cfg.get("enabled"):
        log("captioning images (alt text) for text-aware ranking...")
        n_caps = ranker.caption_items(cap_cfg, profile + candidates)
        log(f"  captioned {n_caps} images")

    log(f"ranking {len(candidates)} candidates, asking for top {top_n}...")
    try:
        picks = ranker.rank(cfg["llm"], profile, candidates, top_n, cfg.get("reranker"))
    except Exception as e:
        log(f"LLM ranking failed: {e}")
        picks = [{"index": i, "score": None, "reason": ""} for i in range(top_n)]

    ordered = []
    used = set()
    for p in picks:
        idx = p.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidates) or idx in used:
            continue
        used.add(idx)
        ordered.append((candidates[idx], p.get("reason", ""), p.get("score")))
    log(f"LLM returned {len(ordered)} usable picks")

    media_dir = os.path.join(cfg["feed"]["output_dir"], "media")
    base = cfg["feed"]["link"]
    for c, _, _ in ordered:
        if c.get("media"):
            c["media"] = mirror_media(c["media"][:4], media_dir, base)

    targets = [(c, c["text"]) for c, _, _ in ordered if c.get("text") and needs_translation(c)]
    if targets:
        log(f"translating {len(targets)} non-Chinese posts...")
        for c, tr in zip((t[0] for t in targets), ranker.translate_to_zh(cfg["llm"], [t[1] for t in targets])):
            c["translation"] = tr

    new_items = [feed.make_item(c, reason, score, cfg["feed"]["link"]) for c, reason, score in ordered]
    old_items = feed.load_items(ITEMS_PATH)
    merged = feed.merge_items(old_items, new_items, cfg["feed"]["max_items_kept"])
    feed.write_outputs(cfg, merged)
    with open(ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    log(f"wrote {len(new_items)} new items; feed now has {len(merged)} total")
    for c, reason, score in ordered[:5]:
        log(f"  [{score}] @{c['author_acct']}: {(c['text'] or '(图)')[:40]} — {reason}")


if __name__ == "__main__":
    sys.exit(main())
