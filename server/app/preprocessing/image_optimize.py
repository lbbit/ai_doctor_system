"""读图 Token 优化（§21）：近无损预压缩 + 多页拼图 + 分辨率自适应。

成本模型：视觉 token ≈ 与像素面积成正比。因此三层手段：

1. **预压缩**：仅当原图超出 ``max_side`` 时缩放，用 LANCZOS 高质量重采样；
   优先 PNG（无损）；仅当字节仍超限时才用高质量 JPEG（q≥90，近无损）。
2. **多页拼图**：同文档连续页拼成一张，把 N 次请求的固定开销压成 1 次。
3. **分辨率自适应**：按 ``detail`` 档位与页面文字密度选倍率。

设计约束：
- **不牺牲可读性**：质量下限 q90；缩放倍率下限 1.0（不放大）
- **不猜内容**：拼图只做几何排布，不改像素内容
- **可关**：全部由配置控制，关闭后行为与改造前一致
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger("app.preprocessing.image_optimize")

# 近无损 JPEG 质量下限（低于此值小字会模糊）
MIN_JPEG_QUALITY = 90
# 拼图后单边上限（像素）：超过会显著增加 token
DEFAULT_TILE_MAX_SIDE = 2200
# 单图字节上限
DEFAULT_MAX_BYTES = 1_500_000


@dataclass
class CompressResult:
    data: bytes
    width: int
    height: int
    format: str
    scaled: bool
    original_bytes: int

    @property
    def ratio(self) -> float:
        if not self.original_bytes:
            return 1.0
        return len(self.data) / self.original_bytes


def _load_image(data: bytes):
    """延迟导入 Pillow（保持模块可被轻量测试导入）。"""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def compress_image(
    data: bytes,
    *,
    max_side: int = 2200,
    max_bytes: int = DEFAULT_MAX_BYTES,
    jpeg_quality: int = 92,
    allow_jpeg: bool = True,
) -> CompressResult:
    """近无损压缩：只在必要时缩放，优先 PNG，超限才降质量 JPEG。

    与「一律 JPEG q75」的区别：后者会让化验单上的小字、上标（如 10⁹/L）
    糊成一团，直接影响识别率。这里把质量下限锁在 90。
    """
    from PIL import Image

    original_bytes = len(data)
    try:
        img = _load_image(data)
    except Exception as exc:  # noqa: BLE001 - 非图片输入直接原样返回
        logger.warning("image.load_failed reason=%s", type(exc).__name__)
        return CompressResult(
            data=data, width=0, height=0, format="unknown",
            scaled=False, original_bytes=original_bytes,
        )

    width, height = img.size
    scaled = False
    longest = max(width, height)
    if max_side > 0 and longest > max_side:
        ratio = max_side / float(longest)
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
        width, height = img.size
        scaled = True

    # 1) 优先 PNG（无损）
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(buf, format="PNG", optimize=True)
    out = buf.getvalue()
    fmt = "png"

    # 2) 超字节上限 → 尝试高质量 JPEG，但**只在确实更小**时采用。
    #    实测发现：某些高对比/细线密集的图，PNG 反而比 JPEG 小得多
    #    （PNG 67749B vs JPEG q90 828895B），盲目转 JPEG 会让体积暴增。
    if len(out) > max_bytes and allow_jpeg:
        quality = max(MIN_JPEG_QUALITY, min(98, jpeg_quality))
        best = out
        best_fmt = fmt
        while quality >= MIN_JPEG_QUALITY:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True, subsampling=0)
            candidate = buf.getvalue()
            if len(candidate) < len(best):
                best, best_fmt = candidate, "jpeg"
            if len(candidate) <= max_bytes:
                break
            quality -= 2
        if best_fmt == "jpeg":
            out, fmt = best, "jpeg"
        if len(out) > max_bytes:
            logger.warning(
                "image.over_budget bytes=%s maxBytes=%s format=%s（已到质量下限，"
                "宁可保留清晰度也不继续降质）",
                len(out), max_bytes, fmt,
            )

    logger.info(
        "image.compressed srcBytes=%s outBytes=%s %sx%s format=%s scaled=%s ratio=%.2f",
        original_bytes, len(out), width, height, fmt, scaled,
        (len(out) / original_bytes) if original_bytes else 1.0,
    )
    return CompressResult(
        data=out, width=width, height=height, format=fmt,
        scaled=scaled, original_bytes=original_bytes,
    )


def adaptive_scale(
    detail: str,
    *,
    text_density: float = 0.5,
    base_scale: float = 1.8,
    min_scale: float = 1.0,
    max_scale: float = 2.5,
) -> float:
    """按 detail 档位与文字密度选渲染倍率（降低稀疏页的无效像素）。

    ``text_density`` 为 0..1 的估算密度（如文本字符数 / 页面面积归一化）。
    """
    detail = (detail or "auto").strip().lower()
    if detail == "low":
        return max(min_scale, min(max_scale, base_scale * 0.6))
    if detail == "high":
        return max(min_scale, min(max_scale, base_scale * 1.3))
    # auto：密度高保倍率，密度低降倍率
    density = max(0.0, min(1.0, text_density))
    factor = 0.7 + 0.6 * density  # 0.7x ~ 1.3x
    return max(min_scale, min(max_scale, base_scale * factor))


def tile_images(
    images: Sequence[bytes],
    *,
    max_side: int = DEFAULT_TILE_MAX_SIDE,
    max_bytes: int = DEFAULT_MAX_BYTES,
    jpeg_quality: int = 92,
) -> List[bytes]:
    """把多页图片拼成尽量少的图（N 页 → ⌈N/k⌉ 张），保持阅读顺序。

    拼接策略：默认**竖向单列**堆叠（最符合文档阅读顺序，且不会因并排
    而让模型误判表格边界）。若单列会超出 ``max_side``，则缩小每页再拼。

    返回压缩后的图片字节列表（每项可直接送模型）。
    """
    if not images:
        return []
    if len(images) == 1:
        return [compress_image(images[0], max_side=max_side, max_bytes=max_bytes,
                               jpeg_quality=jpeg_quality).data]

    from PIL import Image

    loaded = []
    for raw in images:
        try:
            loaded.append(_load_image(raw).convert("RGB"))
        except Exception:  # noqa: BLE001 - 跳过坏图，不影响其余页
            logger.warning("image.tile.skip_bad_image")

    if not loaded:
        return []

    # 每页最大高度：保证拼 2 页后仍不超过 max_side（最保守按 2 页算）
    page_cap = max(200, max_side // 2)
    prepared: List["Image.Image"] = []
    for img in loaded:
        w, h = img.size
        longest = max(w, h)
        target = min(longest, page_cap)
        if target < longest:
            ratio = target / float(longest)
            img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
        prepared.append(img)

    # 贪心装箱：累加高度不超过 max_side 就继续拼
    groups: List[List["Image.Image"]] = []
    current: List["Image.Image"] = []
    current_h = 0
    for img in prepared:
        if current and current_h + img.size[1] > max_side:
            groups.append(current)
            current, current_h = [], 0
        current.append(img)
        current_h += img.size[1]
    if current:
        groups.append(current)

    outputs: List[bytes] = []
    for group in groups:
        width = max(im.size[0] for im in group)
        height = sum(im.size[1] for im in group)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for im in group:
            canvas.paste(im, (0, y))
            y += im.size[1]
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) > max_bytes:
            quality = max(MIN_JPEG_QUALITY, min(98, jpeg_quality))
            buf = io.BytesIO()
            canvas.save(buf, format="JPEG", quality=quality, optimize=True, subsampling=0)
            data = buf.getvalue()
        outputs.append(data)

    logger.info(
        "image.tiled pages=%s tiles=%s bytes=%s",
        len(images), len(outputs), sum(len(o) for o in outputs),
    )
    return outputs


def estimate_image_tokens(
    width: int, height: int, *, detail: str = "auto"
) -> int:
    """粗略估算图像 token（OpenAI 视觉计费口径的近似）。

    采用 512×512 分块估算：每块约 170 token（low 档封顶 85）。
    仅用于预算告警与成本观测，不参与业务判定。
    """
    if width <= 0 or height <= 0:
        return 0
    detail = (detail or "auto").strip().lower()
    if detail == "low":
        return 85
    tiles = math.ceil(width / 512) * math.ceil(height / 512)
    return max(85, tiles * 170)
