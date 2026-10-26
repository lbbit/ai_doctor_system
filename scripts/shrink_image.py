#!/usr/bin/env python
"""图片压缩：读图前先降采样，避免大图占用大量模型上下文。

用法：
  python scripts/shrink_image.py <输入图> [--out 输出路径] [--max-side 1280] [--quality 78]
  python scripts/shrink_image.py docs/testinfo/检查单 --glob "*.jpg"   # 批量

设计要点：
- 长边限制（默认 1280px）+ JPEG 质量 78，医学检验单文字仍可辨认；
- 自动 EXIF 方向纠正，避免手机拍照横躺；
- 输出默认写到 `<原名>.small.jpg`，不覆盖原件（原件只读原则）；
- 打印前后体积与压缩比，便于判断效果。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MAX_SIDE = 1280
DEFAULT_QUALITY = 78


def shrink(src: Path, out: Path, max_side: int, quality: int) -> tuple[int, int]:
    from PIL import Image, ImageOps

    before = src.stat().st_size
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > max_side:
            ratio = max_side / max(img.size)
            img = img.resize(
                (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                Image.LANCZOS,
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {"quality": quality, "optimize": True}
        if out.suffix.lower() in (".jpg", ".jpeg"):
            img.save(out, format="JPEG", **save_kwargs)
        else:
            img.save(out, format="PNG", optimize=True)
    return before, out.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description="压缩图片以便低上下文读图")
    ap.add_argument("paths", nargs="+", help="图片文件或目录")
    ap.add_argument("--out", default=None, help="输出文件（单文件时）")
    ap.add_argument("--out-dir", default=None, help="输出目录（批量时）")
    ap.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE)
    ap.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    args = ap.parse_args()

    targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            targets.extend(
                sorted(q for q in p.iterdir() if q.suffix.lower() in (".jpg", ".jpeg", ".png"))
            )
        elif p.is_file():
            targets.append(p)
        else:
            print(f"[skip] 不存在：{p}", file=sys.stderr)

    if not targets:
        print("[error] 没有可处理的图片", file=sys.stderr)
        return 2

    for src in targets:
        if args.out and len(targets) == 1:
            out = Path(args.out)
        else:
            base = Path(args.out_dir) if args.out_dir else src.parent
            out = base / f"{src.stem}.small{src.suffix}"
        before, after = shrink(src, out, args.max_side, args.quality)
        ratio = (after / before * 100) if before else 0
        print(f"{src.name}: {before // 1024}KB -> {after // 1024}KB ({ratio:.0f}%) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
