"""LLM-as-reranker: fallback heuristic when LLM unavailable."""
import json
import re
import time

import requests


def _log(*a):
    print("[masto-daily]", *a, flush=True)


def _clip(t: str, n: int = 40) -> str:
    t = (t or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "…"


def build_messages(profile, candidates, top_n):
    prof_lines = []
    for i, p in enumerate(profile[:5]):
        t = _clip(p.get("text", ""))
        prof_lines.append(f"P{i}: {t or '(图)'}")
    cand_lines = []
    for i, c in enumerate(candidates[:15]):
        t = _clip(c.get("text", ""))
        cand_lines.append(f"C{i}: {t or '(图)'}")
    prompt = (
        f'返回严格JSON，格式：{{"picks":[{{"index":0,"score":9.0,"reason":"x"}}]}}。\n'
        f"兴趣样例:\n{chr(10).join(prof_lines)}\n"
        f"候选:\n{chr(10).join(cand_lines)}\n"
        f"选最多{top_n}条最符合兴趣，按兴趣从高到低排序。"
    )
    return [{"role": "user", "content": prompt}]


def parse_picks(text: str):
    if not text:
        return []
    t = text
    s = t.find("{")
    e = t.rfind("}")
    if s < 0 or e <= s:
        return []
    try:
        data = json.loads(t[s : e + 1])
    except Exception:
        return []
    picks = data.get("picks") or []
    res = []
    for p in picks:
        if isinstance(p, dict) and "index" in p:
            res.append({"index": p["index"], "score": p.get("score"), "reason": p.get("reason") or ""})
    return res


_ZW = re.compile(r"[\s\u200b\u200c\u200d\ufeff]+")
_WORD = re.compile(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\u0400-\u04ff]")


def _same(a: str, b: str) -> bool:
    return _ZW.sub("", a or "") == _ZW.sub("", b or "")


def _has_words(s: str) -> bool:
    return bool(_WORD.search(s or ""))


def _translate_call(llm_cfg, texts, strict=False, retries=3):
    base = (llm_cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    ask = (
        "把下面每条文本翻译成简体中文。"
        if not strict
        else "把下面每条文本翻译成简体中文；即使很短或看起来无需翻译，也必须给出中文译文，严禁原样照抄。"
    )
    prompt = (
        f"{ask}URL、#话题、:emoji: 保留原样，其余文字翻译。"
        f'只输出 JSON，格式 {{"translations":["第1条译文","第2条译文",...]}}，'
        f"共 {len(texts)} 条，顺序与输入一致。\n{lines}"
    )
    payload = {
        "model": llm_cfg.get("model") or "deepseek-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": llm_cfg.get("max_tokens", 6000),
        "response_format": {"type": "json_object"},
    }
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {llm_cfg.get('api_key')}",
        "Content-Type": "application/json",
    }
    r = None
    last = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            break
        except Exception as e:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            body = ""
            try:
                body = (resp.text or "")[:200] if resp is not None else ""
            except Exception:
                body = ""
            last = f"{type(e).__name__}: {e}" + (f" body={body}" if body else "")
            if attempt + 1 < attempts and status in (429, 500, 502, 503, 504):
                time.sleep(1.0 * (attempt + 1))
    else:
        raise RuntimeError(f"translate request failed after {attempts} attempt(s): {last}")
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    s, e = content.find("{"), content.rfind("}")
    data = json.loads(content[s : e + 1]) if 0 <= s < e else {}
    tr = data.get("translations")
    tr = tr if isinstance(tr, list) else []
    return [
        tr[i] if i < len(tr) and isinstance(tr[i], str) and tr[i].strip() else ""
        for i in range(len(texts))
    ]


def _translate_chunk(llm_cfg, texts, out, strict=False, depth=0):
    """Translate one chunk; on failure split it in half and retry (up to `depth`)."""
    try:
        out.extend(_translate_call(llm_cfg, texts, strict=strict))
    except Exception as e:
        if len(texts) > 1 and depth < 4:
            _log(f"translate: {len(texts)} text(s) failed ({e}); splitting")
            mid = len(texts) // 2
            _translate_chunk(llm_cfg, texts[:mid], out, strict, depth + 1)
            _translate_chunk(llm_cfg, texts[mid:], out, strict, depth + 1)
        else:
            _log(f"translate: giving up on {len(texts)} text(s): {e}")
            out.extend([""] * len(texts))


def translate_to_zh(llm_cfg: dict, texts):
    """Translate texts into Simplified Chinese; untranslatable entries come back as-is.

    Batches the request, splits failing batches, then retries entries that came back
    empty or identical to the source (the model sometimes echoes short/emoji/URL text).
    """
    texts = list(texts or [])
    if not texts or not llm_cfg.get("api_key"):
        return texts

    out = []
    for i in range(0, len(texts), 16):
        _translate_chunk(llm_cfg, texts[i : i + 16], out)

    retry = [i for i, t in enumerate(texts) if _has_words(t) and (not out[i] or _same(out[i], t))]
    if retry:
        res = []
        _translate_chunk(llm_cfg, [texts[i] for i in retry], res, strict=True)
        for j, i in enumerate(retry):
            if j < len(res) and res[j] and not _same(res[j], texts[i]):
                out[i] = res[j]

    n_ok = sum(1 for i in range(len(texts)) if out[i] and not _same(out[i], texts[i]))
    _log(f"translate: {n_ok}/{len(texts)} succeeded")
    return [out[i] if out[i] else texts[i] for i in range(len(texts))]


def resolve_caption_cfg(cfg: dict) -> dict:
    """Merge the `caption` block with `llm` so images can be captioned via the same endpoint.

    Returns {"enabled": False} when captioning is off or unusable.
    """
    cap = dict((cfg or {}).get("caption") or {})
    if not cap.get("enabled", True):
        return {"enabled": False}
    llm = (cfg or {}).get("llm") or {}
    return {
        "enabled": True,
        "base_url": cap.get("base_url") or llm.get("base_url") or "https://api.deepseek.com",
        "api_key": cap.get("api_key") or llm.get("api_key"),
        "model": cap.get("model") or llm.get("model") or "deepseek-flash",
        "max_tokens": cap.get("max_tokens", 1500),
        "per_post": int(cap.get("per_post", 1) or 1),
        "batch": max(1, int(cap.get("batch", 6) or 6)),
        "max_px": int(cap.get("max_px", 512) or 512),
        "max_images": int(cap.get("max_images", 60) or 60),
    }


def _data_url(url: str, max_px: int = 512, timeout: int = 30) -> str:
    """Download an image and return a (downscaled) base64 data URL for a vision model."""
    import base64
    import io

    from PIL import Image

    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    if max_px:
        im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _caption_call(vcfg: dict, data_urls):
    n = len(data_urls)
    prompt = (
        f"下面有 {n} 张图片，编号 1..{n}。"
        "请为每张图片写一句简短的中文描述（alt 文本，20 字以内）。"
        f'只输出 JSON，格式 {{"captions":["第1张描述", ...]}}，共 {n} 条，顺序与图片一致。'
    )
    content: list = [{"type": "text", "text": prompt}]
    for u in data_urls:
        content.append({"type": "image_url", "image_url": {"url": u}})
    payload = {
        "model": vcfg.get("model") or "deepseek-flash",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": vcfg.get("max_tokens", 1500),
    }
    base = (vcfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={
            "Authorization": f"Bearer {vcfg.get('api_key')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    s, e = text.find("{"), text.rfind("}")
    data = json.loads(text[s : e + 1]) if 0 <= s < e else {}
    caps = data.get("captions")
    caps = caps if isinstance(caps, list) else []
    return [caps[i] if i < len(caps) and isinstance(caps[i], str) else "" for i in range(n)]


def caption_items(vcfg: dict, items) -> int:
    """Attach LLM-generated alt text for each item's first image(s) under `_alt`.

    This replaces the CLIP image-embedding side: captions are folded into the text
    used for ranking, so only the text embedding model is needed. Every failure is
    swallowed (returns what succeeded) so ranking still works on text alone.
    """
    if not vcfg or not vcfg.get("enabled", True) or not vcfg.get("api_key"):
        return 0
    per = int(vcfg.get("per_post", 1) or 1)
    batch = max(1, int(vcfg.get("batch", 6) or 6))
    max_px = int(vcfg.get("max_px", 512) or 512)
    targets = []
    for it in items:
        for u in (it.get("media") or [])[:per]:
            if u:
                targets.append((it, u))
    targets = targets[: int(vcfg.get("max_images", 60) or 60)]

    done = 0
    for i in range(0, len(targets), batch):
        chunk = targets[i : i + batch]
        fetched = []
        for idx, (_it, u) in enumerate(chunk):
            try:
                fetched.append((idx, _data_url(u, max_px)))
            except Exception:
                continue
        if not fetched:
            continue
        try:
            caps = _caption_call(vcfg, [d for _, d in fetched])
        except Exception:
            continue
        for (idx, _), cap in zip(fetched, caps):
            cap = (cap or "").strip()
            if not cap:
                continue
            it = chunk[idx][0]
            it["_alt"] = (it.get("_alt", "") + " " + cap).strip()
            done += 1
    return done


def _candidate_text(c: dict) -> str:
    t = (c.get("text") or "").strip() or (c.get("translation") or "").strip()
    tags = " ".join(c.get("tags") or [])
    alt = (c.get("_alt") or "").strip()
    return " ".join(x for x in (t, tags, alt) if x).strip()


def _rank_heuristic(candidates, top_n: int):
    picks = []
    keywords = ["猫", "cat", "ねこ", "にゃ", "萌", "可愛い", "cute"]
    for i, c in enumerate(candidates[:40]):
        txt = (c.get("text") or "").lower()
        if any(k in txt or k in c.get("author_acct", "") for k in keywords) or c.get("media"):
            picks.append({"index": i, "score": 9.5 - len(picks) * 0.1, "reason": "偏好匹配（图/猫相关）"})
        if len(picks) >= top_n:
            break
    if len(picks) < top_n:
        for i in range(len(candidates)):
            if not any(p["index"] == i for p in picks):
                picks.append({"index": i, "score": 8.0, "reason": "多样性推荐"})
            if len(picks) >= top_n:
                break
    return picks


def _encode(model, texts, cfg):
    kw = {"normalize_embeddings": True, "show_progress_bar": False}
    kw.update(cfg.get("encode_kwargs") or {})
    return model.encode(texts, **kw)


def _recency_weights(n, np):
    w = np.array([1.0 / (i + 1) for i in range(n)], dtype=float)
    return w / w.sum() if n and w.sum() else w


def _zscore(vals, np):
    if not np.any(~np.isnan(vals)):
        return np.zeros_like(vals)
    m, s = np.nanmean(vals), np.nanstd(vals)
    if not np.isfinite(s) or s == 0:
        return np.zeros_like(vals)
    return (vals - m) / s


def _image_scores(candidates, prof_imgs, model, np):
    """Recency-weighted image-image cosine of each candidate's first image vs profile images."""
    from PIL import Image

    def enc(paths):
        imgs = [Image.open(p).convert("RGB") for p in paths]
        return np.asarray(model.encode(imgs, normalize_embeddings=True, show_progress_bar=False, batch_size=16))

    pv = enc([p for p, _ in prof_imgs])
    pw = np.array([w for _, w in prof_imgs], dtype=float)
    pw = pw / pw.sum() if pw.sum() else pw

    cand_idx, cand_paths = [], []
    for i, c in enumerate(candidates):
        ip = c.get("_image_paths") or []
        if ip:
            cand_idx.append(i)
            cand_paths.append(ip[0])

    scores = np.full(len(candidates), np.nan)
    if not cand_idx:
        return scores
    sims = enc(cand_paths) @ pv.T               # [n_cand, n_prof_img]
    s = sims @ pw
    for j, i in enumerate(cand_idx):
        scores[i] = float(s[j])
    return scores


def _rank_embeddings(profile, candidates, top_n: int, cfg):
    """Rank candidates by text and (optionally) image similarity to the profile.

    Text and image similarities are computed independently, standardised within each
    modality (z-score) so they become comparable, then combined with per-modality weights
    (default 0.5/0.5). Posts with only one modality use that modality alone; posts with
    neither are left for the diversity fill. Returns None so the caller can fall back to
    the heuristic when embeddings are unavailable.
    """
    if not cfg or not cfg.get("enabled", True) or not candidates:
        return None

    prof_texts = [_candidate_text(p) for p in profile]
    prof_texts = [t for t in prof_texts if t]

    img_cfg = cfg.get("image") or {}
    prof_imgs = []
    if img_cfg.get("enabled", False):
        per = int(img_cfg.get("max_images_per_post", 1) or 1)
        for i, p in enumerate(profile):
            w = 1.0 / (i + 1)
            for path in (p.get("_image_paths") or [])[:per]:
                prof_imgs.append((path, w))
        prof_imgs = prof_imgs[: int(img_cfg.get("max_profile_images", 30) or 30)]

    if not prof_texts and not prof_imgs:
        return None
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None

    ts = np.full(len(candidates), np.nan)
    if prof_texts:
        try:
            tm = SentenceTransformer(cfg["model"], trust_remote_code=bool(cfg.get("trust_remote_code", True)))
            pv = np.asarray(_encode(tm, prof_texts, cfg))
            cand_texts = [_candidate_text(c) for c in candidates]
            idx = [i for i, t in enumerate(cand_texts) if t]
            if idx:
                cv = np.asarray(_encode(tm, [cand_texts[i] for i in idx], cfg))
                s = (cv @ pv.T) @ _recency_weights(len(prof_texts), np)
                for j, i in enumerate(idx):
                    ts[i] = float(s[j])
        except Exception:
            pass

    is_ = np.full(len(candidates), np.nan)
    if prof_imgs:
        try:
            is_ = _image_scores(candidates, prof_imgs, SentenceTransformer(img_cfg["model"]), np)
        except Exception:
            is_ = np.full(len(candidates), np.nan)

    has_t, has_i = ~np.isnan(ts), ~np.isnan(is_)
    if not has_t.any() and not has_i.any():
        return None

    alpha = 0.5
    beta = float(img_cfg.get("weight", 0.5)) if has_i.any() else 0.0
    zt, zi = _zscore(ts, np), _zscore(is_, np)
    combined = np.full(len(candidates), np.nan)
    both = has_t & has_i
    combined[both] = (alpha * zt[both] + beta * zi[both]) / (alpha + beta)
    combined[has_t & ~has_i] = zt[has_t & ~has_i]
    combined[~has_t & has_i] = zi[~has_t & has_i]

    order = np.argsort(np.where(np.isnan(combined), -np.inf, combined))[::-1]
    picks = []
    for i in order:
        if len(picks) >= top_n or np.isnan(combined[i]):
            break
        if has_i[i] and beta:
            tag = "（文本+图）" if has_t[i] else "（图）"
            reason = f"画像相似度 {combined[i]:.2f}{tag}"
        else:
            reason = f"画像相似度 {ts[i]:.2f}"
        picks.append({"index": int(i), "score": round(float(combined[i]), 4), "reason": reason})
    if len(picks) < top_n:
        for i in order:
            if len(picks) >= top_n:
                break
            if any(p["index"] == int(i) for p in picks):
                continue
            picks.append({"index": int(i), "score": None, "reason": "多样性补位"})
    return picks


def rank(llm_cfg: dict, profile, candidates, top_n: int, reranker_cfg=None):
    picks = _rank_embeddings(profile, candidates, top_n, reranker_cfg)
    if picks is None:
        picks = _rank_heuristic(candidates, top_n)
    return picks
