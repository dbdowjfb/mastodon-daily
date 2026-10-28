"""Generate RSS feed + a small index page from accumulated items."""
import html
import json
import os
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

from .masto import strip_html

META_KEYS = ("guid", "title", "link", "pubDate", "description", "score", "source")


def make_item(c: dict, reason: str, score, feed_link=None) -> dict:
    title = f"@{c['author_acct']}: {strip_html(c['text'])[:60] or '(图片)'}"
    desc = f"<p>{html.escape(c['text'] or '')}</p>"
    tr = (c.get("translation") or "").strip()
    if tr and tr != (c.get("text") or "").strip():
        desc += f"<p>（译）{html.escape(tr)}</p>"
    for u in (c.get("media") or [])[:4]:
        desc += f'<p><img src="{html.escape(u)}" /></p>'
    if c.get("url"):
        src = html.escape(c["url"])
        desc += f'<p>原文：<a href="{src}">{src}</a></p>'
    desc += (
        f"<p><small>推荐理由：{html.escape(reason or '')}"
        f" · 来源 {c['source']} · ♥{c['favourites']} ↻{c['reblogs']}</small></p>"
    )
    try:
        pub = parsedate_to_datetime(c["created_at"]).astimezone(timezone.utc)
    except Exception:
        pub = datetime.now(timezone.utc)
    return {
        "guid": c["id"],
        "title": title,
        "link": feed_link or c["url"],
        "source_url": c["url"],
        "pubDate": format_datetime(pub),
        "description": desc,
        "score": score,
        "source": c["source"],
    }


def load_items(path: str):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def merge_items(old, new, max_items):
    seen = set()
    merged = []
    for it in list(new) + list(old):
        g = it.get("guid")
        if not g or g in seen:
            continue
        seen.add(g)
        merged.append(it)
    merged.sort(key=lambda it: parsedate_to_datetime(it["pubDate"]), reverse=True)
    return merged[:max_items]


def _sort_key(it):
    try:
        return parsedate_to_datetime(it["pubDate"]).timestamp()
    except Exception:
        return 0


def render_feed_xml(cfg: dict, items) -> str:
    feed = cfg["feed"]
    rows = []
    for it in items:
        rows.append(
            "    <item>\n"
            f"      <title>{html.escape(it['title'])}</title>\n"
            f"      <link>{html.escape(it['link'])}</link>\n"
            f'      <guid isPermaLink="false">{html.escape(it["guid"])}</guid>\n'
            f"      <pubDate>{it['pubDate']}</pubDate>\n"
            f"      <description>{html.escape(it['description'])}</description>\n"
            "    </item>"
        )
    items_xml = "\n".join(rows)
    now = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(feed['title'])}</title>
    <link>{html.escape(feed['link'])}</link>
    <description>{html.escape(feed['description'])}</description>
    <language>zh-cn</language>
    <lastBuildDate>{now}</lastBuildDate>
{items_xml}
  </channel>
</rss>
"""


def render_index_html(cfg: dict, items) -> str:
    feed = cfg["feed"]
    lis = []
    for it in items:
        lis.append(
            f'<li><a href="{html.escape(it["link"])}">{html.escape(it["title"])}</a>'
            f'<div class="meta">{it["pubDate"][:16]} · {html.escape(it["source"])}'
            f' · score {html.escape(str(it.get("score","")))}</div>'
            f'<div class="desc">{it["description"]}</div></li>'
        )
    return f"""<!doctype html>
<html lang="zh-cn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(feed['title'])}</title>
<style>
 body{{max-width:720px;margin:2rem auto;padding:0 1rem;font:15px/1.6 system-ui,sans-serif;color:#222}}
 h1{{font-size:1.3rem}} a{{color:#1a73e8;text-decoration:none}} a:hover{{text-decoration:underline}}
 ul{{list-style:none;padding:0}} li{{padding:1rem 0;border-bottom:1px solid #eee}}
 .meta{{color:#888;font-size:.8rem;margin:.2rem 0}} .desc img{{max-width:100%;border-radius:6px;margin-top:.4rem}}
 .sub a{{font-size:.9rem}}
</style></head><body>
<h1>{html.escape(feed['title'])}</h1>
<p class="sub">RSS: <a href="feed.xml">feed.xml</a> · {html.escape(feed['description'])}</p>
<ul>
{chr(10).join(lis)}
</ul></body></html>
"""


def write_outputs(cfg: dict, items):
    out_dir = cfg["feed"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(render_feed_xml(cfg, items))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index_html(cfg, items))
