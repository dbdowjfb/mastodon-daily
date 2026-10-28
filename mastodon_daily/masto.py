"""Minimal Mastodon API client + status helpers."""
import hashlib
import html
import os
import re
from urllib.parse import urlparse

import requests

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>\s*<p>", "\n\n", s)
    s = TAG_RE.sub("", s)
    return html.unescape(s).strip()


def _next_link(link: str):
    if not link:
        return None
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) >= 2 and 'rel="next"' in seg[1]:
            return seg[0].strip().strip("<>")
    return None


def media_urls(status: dict, use_preview: bool = True):
    out = []
    for m in status.get("media_attachments") or []:
        u = (m.get("preview_url") if use_preview else None) or m.get("url")
        if u:
            out.append(u)
    return out


ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")


def _media_name(u: str) -> str:
    ext = os.path.splitext(urlparse(u).path)[1].lower()
    if ext not in ALLOWED_EXT:
        ext = ".jpg"
    return hashlib.sha1(u.encode()).hexdigest()[:16] + ext


def _ensure(u: str, out_dir: str) -> str:
    path = os.path.join(out_dir, _media_name(u))
    if not os.path.exists(path):
        r = requests.get(u, timeout=30)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
    return path


def mirror_media(urls, out_dir: str, base_url: str):
    """Download media into out_dir/media and return self-hosted absolute URLs.

    Falls back to the original URL on any failure; skips files already present.
    """
    os.makedirs(out_dir, exist_ok=True)
    base = base_url.rstrip("/")
    out = []
    for u in urls or []:
        try:
            _ensure(u, out_dir)
            out.append(f"{base}/media/{_media_name(u)}")
        except Exception:
            out.append(u)
    return out


def cache_media(urls, limit=None):
    """Download media to a temp cache and return local file paths (for image embedding)."""
    import tempfile

    out_dir = os.path.join(tempfile.gettempdir(), "masto_media_cache")
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for u in urls or []:
        if limit is not None and len(paths) >= limit:
            break
        try:
            paths.append(_ensure(u, out_dir))
        except Exception:
            pass
    return paths


def normalize(status: dict, source: str) -> dict:
    """Flatten a status (resolving reblogs to the original post)."""
    s = status.get("reblog") or status
    acct = s.get("account") or {}
    return {
        "id": s.get("id"),
        "source": source,
        "url": s.get("url") or s.get("uri"),
        "created_at": s.get("created_at"),
        "author_acct": acct.get("acct"),
        "author_name": acct.get("display_name"),
        "text": strip_html(s.get("content", "")),
        "tags": [t.get("name") for t in (s.get("tags") or [])],
        "language": s.get("language"),
        "sensitive": bool(s.get("sensitive")),
        "replies": s.get("replies_count", 0),
        "reblogs": s.get("reblogs_count", 0),
        "favourites": s.get("favourites_count", 0),
        "media": media_urls(s),
    }


class Mastodon:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _all(self, path: str, params: dict, limit: int):
        url = self.base + path
        out = []
        p = dict(params)
        while url and len(out) < limit:
            r = self.session.get(url, params=p, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            url = _next_link(r.headers.get("Link"))
            p = None
        return out[:limit]

    def verify_credentials(self):
        r = self.session.get(self.base + "/api/v1/accounts/verify_credentials", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def favourites(self, limit=40):
        return self._all("/api/v1/favourites", {"limit": min(limit, 40)}, limit)

    def bookmarks(self, limit=40):
        return self._all("/api/v1/bookmarks", {"limit": min(limit, 40)}, limit)

    def boosts(self, account_id, limit=40):
        raw = self._all(
            f"/api/v1/accounts/{account_id}/statuses",
            {"limit": min(limit, 40), "exclude_replies": "true"},
            limit * 2,
        )
        return [s for s in raw if s.get("reblog")][:limit]

    def home(self, limit=40):
        return self._all("/api/v1/timelines/home", {"limit": min(limit, 40)}, limit)

    def public(self, local=True, limit=40):
        return self._all(
            "/api/v1/timelines/public",
            {"limit": min(limit, 40), "local": "true" if local else "false"},
            limit,
        )
