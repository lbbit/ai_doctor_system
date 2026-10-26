"""PDF 页渲染（pypdfium2）：把每页渲染成 PNG，供 VLM 逐页识别。

为什么需要：整份 20 页 PDF 一次性送模型时，网关 60s 超时（实测 121s 失败），
降级到规则解析只能得到噪声草稿；单页图片抽取实测 ~6s 且字段准确。
"""

import io
import logging
from typing import List, Optional, Protocol, Tuple

from app.config.settings import settings

logger = logging.getLogger("app.pdf_render")


class RenderError(RuntimeError):
    """PDF 无法渲染（损坏/加密/缺少依赖）。"""


class PageImage(Protocol):
    page_no: int
    image_bytes: bytes
    mime: str


def pypdfium2_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _render_page(pdf, index: int, scale: float) -> bytes:
    """渲染单页为 PNG；超限则逐级降倍率。"""
    limit = settings.pdf_page_image_max_bytes
    current = scale
    for _ in range(3):
        bitmap = pdf[index].render(scale=current)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= limit or current <= 1.0:
            return data
        current = max(1.0, current * 0.75)
    return data


def render_pdf_pages(
    content: bytes,
    *,
    max_pages: Optional[int] = None,
    scale: Optional[float] = None,
) -> List[Tuple[int, bytes]]:
    """PDF → [(pageNo, pngBytes)]；分批渲染以控制内存峰值。"""
    if not pypdfium2_available():
        raise RenderError("缺少 pypdfium2 依赖，无法渲染 PDF 页图")
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"pypdfium2 加载失败：{exc}") from exc

    limit = max_pages if max_pages is not None else settings.pdf_vision_max_pages
    scale = scale if scale is not None else settings.pdf_render_scale

    try:
        pdf = pdfium.PdfDocument(content)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"PDF 打开失败：{type(exc).__name__}: {exc}") from exc

    try:
        total = len(pdf)
        count = min(total, max(1, limit))
        out: List[Tuple[int, bytes]] = []
        for index in range(count):
            try:
                out.append((index + 1, _render_page(pdf, index, scale)))
            except Exception as exc:  # noqa: BLE001  单页渲染失败不阻塞其它页
                logger.warning("pdf_render.page_failed page=%s err=%s", index + 1, exc)
        return out
    finally:
        close = getattr(pdf, "close", None)
        if callable(close):
            close()
