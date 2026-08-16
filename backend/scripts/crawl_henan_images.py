# -*- coding: utf-8 -*-
"""河南博物院文物品鉴配图下载（图注级入库的前置数据采集）

输入: src/data/henan_museum.json（283 条已爬文本，含 id/name/url）
输出:
  - src/data/images/henan/{文物名}/01.jpg 02.jpg ...（全部配图，DOM 顺序编号）
  - src/data/henan_images.json（图片清单: 文件/图号/图注/栏目/语境段落，供图注级入库）

结构说明（实测）:
  文章页正文为 tab 栏目（深度品鉴/文化解读/比较研究…），段落内联图片:
    <p><img src="..."></p>          ← 图片段落
    <p>图1  甲骨文所见"龙"字写法</p>  ← 图注段落（紧跟在图片后，~89% 配对率）
  本脚本按 DOM 顺序提取 text/img 流，配对图注，保存语境段落。

礼貌爬取: 1.2s 延迟、UA、2 次重试、20s 超时、断点续爬（已有图片的条目跳过）。
规模: 283 条 × ~27 张 ≈ 7600 张 ≈ 1.1GB，预计 4-6 小时。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.chnmus.net"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
JSON_IN = Path(__file__).parent.parent / "src" / "data" / "henan_museum.json"
JSON_OUT = Path(__file__).parent.parent / "src" / "data" / "henan_images.json"
IMAGES_DIR = Path(__file__).parent.parent / "src" / "data" / "images" / "henan"
DELAY = 1.2
RETRIES = 2
TIMEOUT = 20

# 无效字符（Windows 文件名）与保留名
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_dirname(name: str) -> str:
    """文物名 → 安全目录名（清理非法字符，去首尾空白）"""
    return _ILLEGAL.sub("_", name.strip()).rstrip(" .") or "unknown"


def fetch(url: str, retries: int = RETRIES) -> str:
    """GET 页面 HTML（重试 + 超时）"""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"    [retry {attempt+1}] {url[:80]}: {str(e)[:60]}")
            time.sleep(2 * (attempt + 1))
    return ""


def download(url: str, dest: Path) -> bool:
    """下载图片到 dest，成功返回 True"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read()
        if len(data) < 1024:  # 过小的文件视为占位图/失败
            return False
        dest.write_bytes(data)
        return True
    except Exception as e:
        print(f"    [img retry] {url[-60:]}: {str(e)[:60]}")
        return False


def abs_url(src: str) -> str:
    """相对路径补全为绝对 URL"""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return BASE + src
    return src


def parse_page(html: str) -> list[dict]:
    """按 DOM 顺序提取 [text|img] 块流，返回块列表

    返回: [{"type": "text", "text": str} | {"type": "img", "src": str}]
    """
    blocks: list[dict] = []
    for m in re.finditer(r"<(p|h[1-6])[^>]*>(.*?)</\1>", html, re.S):
        raw = m.group(2)
        # 块内图片（可能多张，取第一张；正文段落嵌图为主）
        srcs = re.findall(r'<img[^>]*\bsrc="([^"]+)"', raw)
        real = [s for s in srcs if re.search(r"\.(jpe?g|png|webp)(\?|$)", s, re.I)]
        if real:
            blocks.append({"type": "img", "src": abs_url(real[0])})
        text = re.sub(r"<[^>]+>", "", raw)
        text = re.sub(r"&nbsp;", " ", text).strip()
        if text:
            blocks.append({"type": "text", "text": text})
    return blocks


def pair_captions(blocks: list[dict]) -> dict[int, dict]:
    """图注配对：img 块后最近的 text 块若以 '图N' 开头 → 该图的图注

    返回: {img块索引: {"figure_no": str, "caption": str, "section": str}}
    """
    paired: dict[int, dict] = {}
    for i, b in enumerate(blocks):
        if b["type"] != "img":
            continue
        nxt = next(
            (x["text"] for x in blocks[i + 1:] if x["type"] == "text" and len(x["text"]) > 2),
            "",
        )
        m = re.match(r"^图\s*(\d+)[\s.、:：]*(.*)", nxt)
        if m:
            paired[i] = {
                "figure_no": m.group(1),
                "caption": nxt[:80],
                "section": "",
            }
    return paired


def nearest_section(html: str, pos: int) -> str:
    """尽力提取图片所属栏目（tab 标题）；找不到返回空串"""
    prev = html[:pos]
    m = re.findall(r'<div class="nav-item"><a href="javascript:;">([^<]+)</a></div>', prev)
    return m[-1] if m else ""


def retry_failed(manifest: dict) -> tuple[int, int, int]:
    """补漏模式：重新解析每条页面，只下载缺失的图（跳过已有文件）

    主模式断点续爬按"目录非空"跳过整条——条目内某张图失败时，
    该条已入 manifest，重跑会被跳过，失败图永不重试。
    本模式逐条 refetch 页面 → 与磁盘已下载文件对比 → 只补缺失，
    同时重建图注信息（配对可能因网络波动改善）。
    """
    refetched = fixed = still_failed = 0
    for key, entry in manifest.items():
        out_dir = IMAGES_DIR / key
        existing = {f.name for f in out_dir.iterdir()} if out_dir.exists() else set()
        url = entry.get("url", "")
        html = fetch(url)
        if not html:
            still_failed += 1
            print(f"  {key}: 页面获取失败，跳过")
            continue
        blocks = parse_page(html)
        paired = pair_captions(blocks)
        refetched += 1

        seq = 0
        updated: list[dict] = []
        for idx, b in enumerate(blocks):
            if b["type"] != "img":
                continue
            seq += 1
            ext = re.search(r"\.(jpe?g|png|webp)", b["src"], re.I)
            fname = f"{seq:02d}.{ext.group(1).lower() if ext else 'jpg'}"
            pinfo = paired.get(idx, {})
            pinfo = {
                "figure_no": pinfo.get("figure_no", ""),
                "caption": pinfo.get("caption", ""),
                "section": pinfo.get("section", ""),
                "context": "",
            }
            if fname in existing:
                updated.append({"file": fname, **pinfo})
                continue
            dest = out_dir / fname
            ok = download(b["src"], dest)
            time.sleep(DELAY)
            if ok:
                fixed += 1
                updated.append({"file": fname, **pinfo})
            else:
                still_failed += 1
                print(f"    ✗ {key}/{fname} 重试仍失败: {b['src'][-60:]}")

        manifest[key] = {**entry, "count": len(updated), "images": updated}
        tmp = JSON_OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(JSON_OUT)
    return refetched, fixed, still_failed


def main():
    # ── 补漏模式：只重下缺失的图 ──
    if "--retry-failed" in sys.argv:
        print("=== 补漏模式：只下载缺失的图 ===\n")
        manifest = {}
        if JSON_OUT.exists():
            manifest = json.loads(JSON_OUT.read_text(encoding="utf-8"))
        if not manifest:
            print("manifest 为空——先跑主模式")
            return
        refetched, fixed, still = retry_failed(manifest)
        print(f"\n=== 补漏完成：重解析 {refetched} 条，补下 {fixed} 张，仍失败 {still} 张 ===")
        print(f"（再跑一次 --retry-failed 可继续补仍失败的）")
        return

    print("=== 河南博物院配图下载 ===\n")
    data = json.loads(JSON_IN.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else list(data.values())
    print(f"共 {len(items)} 条文物")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    if JSON_OUT.exists():
        manifest = json.loads(JSON_OUT.read_text(encoding="utf-8"))
        print(f"断点续爬：已有 {len(manifest)} 条完成")

    skipped = failed = 0
    for i, item in enumerate(items, 1):
        name = item.get("name", "?")
        key = safe_dirname(name)
        out_dir = IMAGES_DIR / key
        # 断点续爬：manifest 已有该条目且目录非空 → 跳过
        if key in manifest and out_dir.exists() and any(out_dir.iterdir()):
            skipped += 1
            print(f"[{i}/{len(items)}] {name}: 跳过（已有 {len(manifest[key]['images'])} 图）")
            continue

        url = item.get("url", "")
        if not url:
            failed += 1
            print(f"[{i}/{len(items)}] {name}: 无 URL，跳过")
            continue

        html = fetch(url)
        if not html:
            failed += 1
            print(f"[{i}/{len(items)}] {name}: 页面获取失败")
            continue

        blocks = parse_page(html)
        paired = pair_captions(blocks)

        out_dir.mkdir(parents=True, exist_ok=True)
        images: list[dict] = []
        seq = 0
        for idx, b in enumerate(blocks):
            if b["type"] != "img":
                continue
            seq += 1
            ext = re.search(r"\.(jpe?g|png|webp)", b["src"], re.I)
            fname = f"{seq:02d}.{ext.group(1).lower() if ext else 'jpg'}"
            dest = out_dir / fname
            if dest.exists() and dest.stat().st_size > 1024:
                ok = True  # 已下载过
            else:
                ok = download(b["src"], dest)
                time.sleep(DELAY)
            if not ok:
                print(f"    ✗ 图{seq} 下载失败: {b['src'][-60:]}")
                failed += 1
                continue
            pinfo = paired.get(idx, {})
            images.append({
                "file": fname,
                "figure_no": pinfo.get("figure_no", ""),
                "caption": pinfo.get("caption", ""),
                "section": pinfo.get("section", ""),
                "context": "",  # 语境段落入库时由入库脚本回填
            })
            time.sleep(DELAY)

        manifest[key] = {"name": name, "url": url, "count": len(images), "images": images}
        # 增量落盘（断点续爬安全）
        tmp = JSON_OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(JSON_OUT)
        print(f"[{i}/{len(items)}] {name}: {len(images)} 图 ✓（跳过累计 {skipped} / 失败累计 {failed}）")

    print(f"\n=== 完成：{len(manifest)} 条，跳过 {skipped}，失败 {failed} ===")
    print(f"图片清单: {JSON_OUT}")
    print(f"图片目录: {IMAGES_DIR}")


if __name__ == "__main__":
    main()
