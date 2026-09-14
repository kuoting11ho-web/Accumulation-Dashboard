"""
fetch_news.py — 讀 feeds.txt，抓最近的新聞，寫成 brief.json

本機測試：
    pip install feedparser
    python fetch_news.py
    # 產生 brief.json，用瀏覽器打開 dashboard 的 NEWS 分頁就看得到

GitHub Actions 會每天自動跑一次（見 .github/workflows/news.yml）。
"""

import json
import time
from datetime import datetime, timezone

import feedparser

FEEDS_FILE = "feeds.txt"
OUT_FILE = "brief.json"

MAX_AGE_HOURS = 36        # 幾小時內的算「新」
PER_SOURCE = 4            # 每個來源最多取幾則
PER_STREAM = 12           # 每個分類最多留幾則

STREAMS = {
    "world": "世界",
    "ai": "AI",
    "sport": "運動",
}


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


def entry_time(entry):
    """盡量取出發布時間，取不到就回 None"""
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return datetime.fromtimestamp(time.mktime(tm), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return None


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
                age = (now - published).total_seconds() / 3600
                if age > MAX_AGE_HOURS:
                    continue
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            buckets[stream].append({
                "title": title,
                "url": link,
                "source": name,
                "published": published.isoformat() if published else None,
                "_sort": published.timestamp() if published else 0,
            })
            kept += 1
        print(f"  ✓ {name} — 取 {kept} 則")

    # 每個分類依時間排序、去掉重複標題、截斷
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
    print(f"\n寫入 {OUT_FILE}：共 {total} 則")
    for key, s in out_streams.items():
        print(f"  {s['label']}: {len(s['items'])}")
    if failures:
        print(f"\n失敗的來源（{len(failures)}）— 考慮在 feeds.txt 註解掉：")
        for f_ in failures:
            print(f"  - {f_}")


if __name__ == "__main__":
    main()
