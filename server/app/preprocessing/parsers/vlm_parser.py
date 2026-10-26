"""P0 默认解析器：VLM 逐页直读（PDF 按页渲染成图 → 单页抽取 → 合并去重）。

关键设计（2026-10-21 真实样本实测）：
- 整份多页 PDF 一次性送模型会触发网关 60s 超时（20 页实测 121s 失败）；
  单页图片抽取仅约 6s，且字段名/值/参考区间更准确。
- 单页失败不放弃整份：保留成功页结果，并把失败页写入 page_errors 供前端展示。
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from app.common.errors import LLMTimeoutError, LLMUnavailableError
from app.config.settings import settings
from app.llm.extraction import extract_report_payload
from app.preprocessing.extractor import validate_derived_record
from app.preprocessing.noise import is_noise_measurement
from app.preprocessing.parser_base import PageAsset, ParseInput, ParserResult

logger = logging.getLogger("app.parsers.vlm")

_VLM_TIMEOUT_HINT = "AI 视觉识别超时"


class VlmParser:
    """首选解析器：LLM 不可用 → available()=False → 注册表降级 rule。"""

    name = "vlm"
    supported_doc_types = {"lab_report", "imaging_report", "visit_record", "other"}

    def available(self) -> bool:
        try:
            from app.llm.client import get_llm_client

            return get_llm_client("extraction").available()
        except Exception:  # noqa: BLE001
            return False

    def parse(self, inp: ParseInput) -> ParserResult:
        from app.llm.client import get_llm_client

        llm = inp.get("llm_client") or get_llm_client("extraction")
        pages: List[PageAsset] = inp.get("pages") or []
        doc_type = inp.get("doc_type") or "other"
        progress = inp.get("progress")
        document_id = inp.get("document_id")

        if not pages:
            return _fail("无可解析内容")

        text_all = "\n".join((p.get("text") or "") for p in pages).strip()
        work_pages = self._prepare_pages(inp, pages, doc_type)
        if not work_pages:
            return _fail("无可解析内容（无文本且无图片）")

        total = len(work_pages)
        results: List[Optional[dict]] = [None] * total
        errors: List[str] = []
        concurrency = max(1, min(settings.pdf_vision_concurrency, total))
        started = time.perf_counter()

        def _run(index: int) -> None:
            page = work_pages[index]
            page_text = (page.get("text") or "").strip()
            image_bytes = page.get("image_bytes")
            mime = page.get("mime") or "image/png"
            if not page_text and not image_bytes:
                return
            try:
                raw = extract_report_payload(
                    llm,
                    content=page_text,
                    doc_type=doc_type,
                    image_bytes=image_bytes,
                    mime=mime if image_bytes else None,
                )
                results[index] = raw
            except LLMUnavailableError as exc:
                errors.append(f"第{page.get('page_no')}页：LLM 不可用（{exc.message}）")
            except LLMTimeoutError:
                errors.append(f"第{page.get('page_no')}页：{_VLM_TIMEOUT_HINT}，可点“重新抽取”")
            except Exception as exc:  # noqa: BLE001  (网关/JSON 错误 → 记录并继续)
                logger.warning(
                    "vlm parse error doc=%s page=%s: %s",
                    document_id, page.get("page_no"), exc,
                )
                errors.append(
                    f"第{page.get('page_no')}页：{type(exc).__name__}: {str(exc)[:120]}"
                )
            finally:
                if callable(progress):
                    done = sum(1 for r in results if r is not None) + len(errors)
                    progress(min(done, total), total)

        if concurrency == 1 or total == 1:
            for i in range(total):
                _run(i)
                if callable(progress):
                    progress(i + 1, total)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(_run, range(total)))
            if callable(progress):
                progress(total, total)

        merged = _merge_pages(results, doc_type)
        if merged is None:
            detail = "；".join(dict.fromkeys(errors)) or "无有效抽取结果"
            return _fail(f"VLM 抽取失败：{detail}")

        logger.info(
            "vlm.done doc=%s pages=%s failedPages=%s obs=%s events=%s elapsedMs=%.0f",
            document_id,
            total,
            len(errors),
            len(merged["observations"]),
            len(merged["events"]),
            (time.perf_counter() - started) * 1000,
        )
        out: ParserResult = {
            "parser_name": self.name,
            "report_date": merged["report_date"],
            "doc_type": merged["doc_type_suggest"] or doc_type,
            "events": merged["events"],
            "observations": merged["observations"],
            "source_text": text_all,
            "success": True,
            "error": None,
            "page_count": total,
        }
        if errors:
            out["page_errors"] = list(dict.fromkeys(errors))[:10]
        return out

    def _prepare_pages(
        self, inp: ParseInput, pages: List[PageAsset], doc_type: str
    ) -> List[PageAsset]:
        """纯文本多页 PDF 先渲染页图走视觉通道；无渲染能力则逐页文本。"""
        if any(p.get("image_bytes") for p in pages):
            return pages

        pdf_render = inp.get("pdf_render")
        if callable(pdf_render):
            try:
                rendered = pdf_render()
            except Exception as exc:  # noqa: BLE001
                logger.info("vlm.render_skip doc=%s err=%s", inp.get("document_id"), exc)
                rendered = None
            if rendered:
                out: List[PageAsset] = []
                for page_no, image_bytes in rendered:
                    src = next(
                        (p for p in pages if p.get("page_no") == page_no), None
                    )
                    out.append(
                        {
                            "page_no": page_no,
                            "image_bytes": image_bytes,
                            "mime": "image/png",
                            "text": (src or {}).get("text") or "",
                        }
                    )
                return out

        return [p for p in pages if (p.get("text") or "").strip()]


def _merge_pages(raw_pages: List[Optional[dict]], doc_type: str) -> Optional[dict]:
    """逐页结果合并：去重（同名同值同日期）、过滤噪声指标、补报告日期。"""
    if not any(raw_pages):
        return None

    observations: List[dict] = []
    events: List[dict] = []
    seen_obs: set = set()
    seen_events: set = set()
    report_date: Optional[str] = None
    doc_type_suggest = ""

    for raw in raw_pages:
        if not raw:
            continue
        try:
            payload = validate_derived_record(raw, doc_type)
        except ValueError:
            continue
        report_date = report_date or payload.get("report_date")
        doc_type_suggest = doc_type_suggest or payload.get("doc_type_suggest") or ""
        for obs in payload.get("observations") or []:
            if is_noise_measurement(obs.get("raw_name") or ""):
                continue
            key = (
                obs.get("raw_name"),
                str(obs.get("value")),
                obs.get("unit"),
                obs.get("occurred_at") or "",
            )
            if key in seen_obs:
                continue
            seen_obs.add(key)
            observations.append(obs)
        for ev in payload.get("events") or []:
            key = (ev.get("type"), ev.get("title"), ev.get("occurred_at"))
            if key in seen_events:
                continue
            seen_events.add(key)
            events.append(ev)

    if not observations and not events:
        return None

    for obs in observations:
        if not obs.get("occurred_at") and report_date:
            obs["occurred_at"] = report_date
    for ev in events:
        if not ev.get("occurred_at") and report_date:
            ev["occurred_at"] = report_date

    return {
        "report_date": report_date,
        "doc_type_suggest": doc_type_suggest,
        "events": events,
        "observations": observations,
        "mock": False,
    }


def _fail(error: str) -> ParserResult:
    return {
        "parser_name": "vlm",
        "report_date": None,
        "doc_type": "",
        "events": [],
        "observations": [],
        "source_text": "",
        "success": False,
        "error": error,
    }
