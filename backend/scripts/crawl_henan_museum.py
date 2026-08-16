# -*- coding: utf-8 -*-
"""Crawl Henan Museum "文物品鉴" (appraisal) articles.

Source: https://www.chnmus.net/ch/collection/appraise/index.html
List pages (pageIndex=1..N) link to detail pages via /content/redirect?id=.
Detail pages contain curatorial prose: dimensions, provenance, connoisseurship,
craft analysis, historical background (the exact deep text our RAG lacks).

Pipeline:
  1. Crawl list pages until no new cards are found.
  2. Crawl each detail page, extract title + prose sections.
  3. Incrementally persist to src/data/henan_museum.json (resume-safe).

Polite crawling: 1.2s delay, UA header, 2 retries, resume from saved JSON.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.chnmus.net"
LIST_URL = BASE + "/ch/collection/appraise/index.html?pageIndex={n}#list"
DETAIL_URL = BASE + "/content/redirect?id={cid}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT = Path(__file__).parent.parent / "src" / "data" / "henan_museum.json"
DELAY = 1.2
MAX_LIST_PAGES = 40

# Nav / chrome words to filter out of the prose (page has lots of boilerplate)
NAV_WORDS = [
    "OA", "资讯公告", "概况", "章程", "培训", "学会", "华夏古乐团", "云端古乐厅",
    "院刊", "动态", "展览", "活动", "服务", "登录", "注册", "长辈版", "智慧导览",
    "相关链接", "作者简介", "趣味猜想", "文物名片", "深度品鉴", "文化解读", "比较研究",
    "参考文献", "博物院内", "博物院官网", "版权所有", "技术支持", "豫ICP", "微信",
    "微博", "地址", "电话", "开放时间", "预约", "参观须知", "志愿者", "文创",
]


def fetch(url: str, retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"  [retry {attempt+1}] {url}: {str(e)[:80]}")
            time.sleep(2 * (attempt + 1))
    return ""


def parse_list(html: str) -> list[dict]:
    """Extract {id, name} from a list page."""
    cards = re.findall(
        r'<a[^>]*href="//www\.chnmus\.net/content/redirect\?id=(\d+)"[^>]*>(.*?)</a>',
        html, re.S,
    )
    items = []
    for cid, inner in cards:
        name = re.sub(r"<[^>]+>", " ", inner)
        name = re.sub(r"\s+", " ", name).strip()
        if name:
            items.append({"id": cid, "name": name})
    return items


def parse_detail(html: str) -> list[str]:
    """Extract prose sections from a detail page."""
    # Title: "文物名 - 河南博物院"
    m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.S)
    title = re.sub(r"\s*-\s*河南博物院.*$", "", m.group(1)).strip() if m else ""

    # Paragraphs + heading lines (section titles like "一、...")
    blocks = re.findall(r"<(p|h1|h2|h3|h4)[^>]*>(.*?)</\1>", html, re.S)
    sections = []
    for tag, content in blocks:
        text = re.sub(r"<[^>]+>", "", content)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) <= 1:
            continue
        if any(w in text for w in NAV_WORDS):
            continue
        sections.append(text)

    # Prepend title if it reads like an artifact name
    if title and len(title) <= 40 and not any(w in title for w in NAV_WORDS):
        sections.insert(0, title)
    return sections


def load_existing() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save(data: dict):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(OUT)


def main():
    print("=== Crawl Henan Museum (文物品鉴) ===\n")
    data = load_existing()
    print(f"Resumed with {len(data)} artifacts already saved")

    # ── Phase 1: list pages ──
    discovered = {}
    for page in range(1, MAX_LIST_PAGES + 1):
        html = fetch(LIST_URL.format(n=page))
        if not html:
            print(f"[list] page {page}: fetch failed, stopping")
            break
        cards = parse_list(html)
        fresh = [c for c in cards if c["id"] not in discovered]
        for c in cards:
            discovered[c["id"]] = c["name"]
        print(f"[list] page {page}: {len(cards)} cards ({len(fresh)} new, total {len(discovered)})")
        if not fresh:
            break
        time.sleep(DELAY)

    # ── Phase 2: detail pages ──
    todo = [cid for cid in discovered if cid not in data]
    print(f"\n[detail] {len(todo)} to crawl, {len(data)} already done\n")

    for i, cid in enumerate(todo):
        html = fetch(DETAIL_URL.format(cid=cid))
        if not html:
            print(f"  [{i+1}/{len(todo)}] {discovered[cid]}: fetch failed, skipped")
            continue
        sections = parse_detail(html)
        data[cid] = {
            "id": cid,
            "name": discovered[cid],
            "url": DETAIL_URL.format(cid=cid),
            "sections": sections,
            "full_text": "\n".join(sections),
        }
        save(data)  # incremental persist (resume-safe)
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(todo)}] saved {len(data)} artifacts "
                  f"(last: {discovered[cid]}, {len(sections)} sections)")
        time.sleep(DELAY)

    # ── Report ──
    total_chars = sum(len(a["full_text"]) for a in data.values())
    nonempty = sum(1 for a in data.values() if len(a["sections"]) > 1)
    print(f"\n=== Done: {len(data)} artifacts ===")
    print(f"Total chars: {total_chars}")
    print(f"With prose (>1 section): {nonempty}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
