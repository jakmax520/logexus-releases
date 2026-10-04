#!/usr/bin/env python3
"""源 #2（YouMind）效果图下载 + 480px WebP 缩略图。

**自包含**：只读同目录的 `sources.json`，不依赖任何外部仓库。
（本仓库的 Actions 用的是只对本仓库有效的 GITHUB_TOKEN，读不到私有的
Logexus 仓库 —— 所以清单随本目录一起版本化，而不是每次去远端拉。）

    python3 ym/fetch.py --dry-run     # 只列清单，不联网
    python3 ym/fetch.py               # 下载到 ym/images 与 ym/thumbs

本地跑时若源图床被墙（cms-assets.youmind.com 在部分地区被 DNS 层面封锁），
`export HTTPS_PROXY=http://127.0.0.1:<端口>` 即可 —— urllib 默认认这个变量。

下载逻辑与 Logexus 仓库的 `products/tauri-app/scripts/make-ym-thumbs.py` 同源；
那边为本地代理场景走 curl，这边在 CI 里直连故走 urllib。**改一处要同步改另一处。**
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.json"

RETRIES = 3
WORKERS = 6
UA = "logexus-releases-mirror/1.0 (+https://github.com/jakmax520/logexus-releases)"


def load_sources() -> list[str]:
    if not SOURCES.exists():
        sys.exit(f"找不到 {SOURCES}")
    urls = json.loads(SOURCES.read_text(encoding="utf-8")).get("images", [])
    if not urls:
        sys.exit("sources.json 里没有 images")
    return urls


def download(url: str) -> bytes:
    """带退避重试地下载。错误信息要带上 HTTP 状态码 —— 只报「失败」等于没说。"""
    last = ""
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status == 200:
                    return resp.read()
                last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(1.0 * (2**attempt))
    raise RuntimeError(f"下载失败：{last}")


def make_thumb(data: bytes, width: int, quality: int) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if w > width:
        im = im.resize((width, round(h * width / w)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def one(url: str, root: Path, width: int, quality: int) -> tuple[str, int, int]:
    name = url.rsplit("/", 1)[-1]
    raw = download(url)
    # 被墙的网络常见「返回 HTML 错误页但状态码 200」，真解一次图才能挡住它进图床
    try:
        thumb = make_thumb(raw, width, quality)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"不是有效图片（{len(raw)} 字节）：{exc}") from exc

    img_dest = root / "ym" / "images" / name
    thumb_dest = root / "ym" / "thumbs" / Path(name).with_suffix(".webp")
    img_dest.parent.mkdir(parents=True, exist_ok=True)
    thumb_dest.parent.mkdir(parents=True, exist_ok=True)
    img_dest.write_bytes(raw)
    thumb_dest.write_bytes(thumb)
    return name, len(raw), len(thumb)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE.parent), help="仓库根（默认脚本的上一级）")
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--quality", type=int, default=72)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    urls = load_sources()
    print(f"清单: {SOURCES}（{len(urls)} 张）")

    if args.dry_run:
        for u in urls:
            print("  " + u.rsplit("/", 1)[-1])
        return

    root = Path(args.root).resolve()
    print(f"输出: {root / 'ym'}\n")

    ok = 0
    failed: list[str] = []
    raw_total = 0
    thumb_total = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(one, u, root, args.width, args.quality): u for u in urls}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                _, raw_len, thumb_len = fut.result()
                ok += 1
                raw_total += raw_len
                thumb_total += thumb_len
            except Exception as exc:  # noqa: BLE001
                failed.append(futures[fut])
                print(f"  ✗ {futures[fut].rsplit('/', 1)[-1]}: {exc}", file=sys.stderr)
            if i % 25 == 0 or i == len(urls):
                print(f"  进度 {i}/{len(urls)}  成功 {ok}  失败 {len(failed)}")

    print()
    print("源 #2 镜像报告")
    print(f"  成功 {ok} / {len(urls)}")
    if ok:
        print(
            f"  原图 {raw_total / 1048576:.1f} MB → 缩略图 {thumb_total / 1048576:.1f} MB"
            f"（均 {thumb_total / ok / 1024:.1f} KB/张，压缩到 1/{raw_total / thumb_total:.1f}）"
        )
    if failed:
        print(f"  失败 {len(failed)} 张 —— 重跑本脚本即可补齐")
        sys.exit(1)


if __name__ == "__main__":
    main()
