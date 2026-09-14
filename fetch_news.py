"""
fetch_news.py — 讀 feeds.txt，抓最近的新聞，寫成 brief.json

本機測試：
    pip install feedparser
    python fetch_news.py

GitHub Actions 每天自動跑一次（見 .github/workflows/news.yml）。
"""

import html
import json
import re
import time
from datetime import datetime, timezone

import feedparser

FEEDS_FILE = "feeds.txt"
OUT_FILE = "brief.json"

MAX_AGE_HOURS = 36        # 幾小時內的算「新」
PER_SOURCE = 4            # 每個來源最多取幾則
PER_STREAM = 16           # 每個分類最多留幾則
SUMMARY_CHARS = 200       # 摘要截斷長度

STREAMS = {
    "world": "世界",
    "ai": "AI",
    "sport": "運動",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


# ── 解析輔助 ────────────────────────────────────────────────

def clean_text(raw):
    """去掉 HTML 標籤、還原跳脫字元、壓平空白、截斷"""
    if not raw:
        return ""
    text = TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text).strip()
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rstrip() + "…"
    return text


def pick_summary(entry, title):
    """取摘要；跟標題幾乎一樣的就不要"""
    candidates = []
    for key in ("summary", "description"):
        if entry.get(key):
            candidates.append(entry[key])
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        candidates.append(content[0]["value"])

    for raw in candidates:
        text = clean_text(raw)
        if not text:
            continue
        if text.rstrip("…").strip().lower() == title.strip().lower():
            continue
        return text
    return ""


def pick_image(entry):
    """依序從常見欄位找縮圖，找不到回空字串"""
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key)
        if isinstance(media, list):
            for m in media:
                url = m.get("url")
                if url and url.startswith("http"):
                    return url

    for enc in entry.get("enclosures") or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]

    blobs = []
    for key in ("summary", "description"):
        if entry.get(key):
            blobs.append(entry[key])
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        blobs.append(content[0]["value"])
    for blob in blobs:
        m = IMG_RE.search(blob)
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return ""


def entry_time(entry):
    """盡量取出發布時間，取不到回 None"""
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return datetime.fromtimestamp(time.mktime(tm), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return None


def load_feeds(path):
    """讀 feeds.txt，回傳 [(stream, name, url), ...]"""
    feeds = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                print(f"  ! 第 {lineno} 行格式不對，略過： {line}")
                continue
            stream, name, url = (p.strip() for p in parts)
            if stream not in STREAMS:
                print(f"  ! 第 {lineno} 行分類 '{stream}' 不認得，略過")
                continue
            feeds.append((stream, name, url))
    return feeds


# ── 主流程 ──────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    feeds = load_feeds(FEEDS_FILE)
    print(f"讀到 {len(feeds)} 個來源\n")

    buckets = {k: [] for k in STREAMS}
    failures = []

    for stream, name, url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:                      # noqa: BLE001
            failures.append(f"{name}: {exc}")
            print(f"  ✕ {name} — {exc}")
            continue

        entries = parsed.entries or []
        if not entries:
            reason = getattr(parsed, "bozo_exception", "沒有項目")
            failures.append(f"{name}: {reason}")
            print(f"  ✕ {name} — {reason}")
            continue

        kept = 0
        for e in entries:
            if kept >= PER_SOURCE:
                break
            published = entry_time(e)
            if published is not None:
                if (now - published).total_seconds() / 3600 > MAX_AGE_HOURS:
                    continue
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            buckets[stream].append({
                "title": title,
                "summary": pick_summary(e, title),
                "image": pick_image(e),
                "url": link,
                "source": name,
                "published": published.isoformat() if published else None,
                "_sort": published.timestamp() if published else 0,
            })
            kept += 1
        print(f"  ✓ {name} — 取 {kept} 則")

    out_streams = {}
    for key, label in STREAMS.items():
        items = sorted(buckets[key], key=lambda x: x["_sort"], reverse=True)
        seen, deduped = set(), []
        for it in items:
            sig = it["title"].lower()
            if sig in seen:
                continue
            seen.add(sig)
            it.pop("_sort", None)
            deduped.append(it)
            if len(deduped) >= PER_STREAM:
                break
        out_streams[key] = {"label": label, "items": deduped}

    payload = {
        "generated": now.isoformat(),
        "streams": out_streams,
        "failures": failures,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    total = sum(len(s["items"]) for s in out_streams.values())
    with_sum = sum(1 for s in out_streams.values() for i in s["items"] if i["summary"])
    with_img = sum(1 for s in out_streams.values() for i in s["items"] if i["image"])
    print(f"\n寫入 {OUT_FILE}：共 {total} 則（有摘要 {with_sum} · 有圖 {with_img}）")
    for s in out_streams.values():
        print(f"  {s['label']}: {len(s['items'])}")
    if failures:
        print(f"\n失敗的來源（{len(failures)}）— 考慮在 feeds.txt 註解掉：")
        for f_ in failures:
            print(f"  - {f_}")


if __name__ == "__main__":
    main()
