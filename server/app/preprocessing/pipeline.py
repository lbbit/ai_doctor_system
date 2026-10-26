"""预处理编排：分类 → 解析 → 字典映射 → 写 DerivedRecord（§5.1/§3.3 状态机）。

可观测性（F-05 体验，2026-10-21）：job 写入 stage/progress 并定期续租，
使前端能展示“读取原件 / 第 n 页识别中 / 字典映射”和执行时长；
worker 崩溃后由 `app.worker.recover_stale_jobs` 依据租约回收。
"""

import logging
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import StateInvalidError
from app.common.ids import new_id
from app.config.settings import settings
from app.db.models import (
    DerivedRecord,
    Document,
    DocumentPage,
    ExtractionJob,
)
from app.documents.service import assert_transition
from app.documents.storage import get_storage_client

# 每份文档保留的草稿版本数（含最新）。
# 为什么需要上限：`derived_records` 每次重抽都 **新增一行**（审计需要能追溯
# 每次抽取结果），但反复重抽会无限堆积（实测单份最多已到 7 版，且每版都带
# 最多 200KB source_text）。保留最近 N 版既能追溯近期调整，又不会无界增长。
DEFAULT_KEEP_VERSIONS = 5


def prune_derived_records(
    db: Session, document_id: str, keep: int = DEFAULT_KEEP_VERSIONS
) -> int:
    """删除某文档**较旧**的草稿版本，保留最近 ``keep`` 个。返回删除行数。

    安全约束：
    - 最新版本永不删除（前端与导出都以它为准）；
    - 已确认的版本不删（`confirmed_at IS NOT NULL`），避免抹掉 HITL 审计线索；
    - ``keep < 1`` 视为不清理（防止误传 0 把历史全删）。
    """
    if keep < 1:
        return 0
    versions = db.scalars(
        select(DerivedRecord)
        .where(DerivedRecord.document_id == document_id)
        .order_by(DerivedRecord.version.desc())
    ).all()
    if len(versions) <= keep:
        return 0

    removable = [
        v for v in versions[keep:]
        if getattr(v, "confirmed_at", None) is None
    ]
    for row in removable:
        db.delete(row)
    if removable:
        db.flush()
    return len(removable)
from app.preprocessing.mapper import map_payload
from app.preprocessing.parsers import select_parser_chain
from app.preprocessing.parser_base import PageAsset, ParseInput, ParserResult

logger = logging.getLogger("app.pipeline")

# ---- 分类分流关键词（AC-4.2）----
_CLASSIFY = [
    ("lab_report", re.compile(r"化验|检验|血常规|尿常规|生化|血糖|血脂|肝功|肾功|甲功|肿瘤标志|凝血|报告单.*(检验|化验)", re.I)),
    ("imaging_report", re.compile(r"影像|CT|MRI|磁共振|超声|B超|X线|X光|DR|钼靶|PET|内镜|病理", re.I)),
    ("visit_record", re.compile(r"病历|门诊|住院|入院|出院|就诊|处方|诊断证明|手术记录", re.I)),
]


def classify_document(filename: str, text: str) -> str:
    """doc_type 分类分流：化验单/影像报告/病历/其他（文件名+文本启发式）。"""
    blob = f"{filename}\n{text[:4000]}"
    for doc_type, pattern in _CLASSIFY:
        if pattern.search(blob):
            return doc_type
    return "other"


def build_pages(document: Document, content: bytes) -> List[PageAsset]:
    """构建分页资产：PDF→pypdf 文本（页图在解析阶段按需渲染）；
    图片→Pillow 归一化（EXIF 方向）单页。"""
    pages: List[PageAsset] = []
    if document.kind == "PDF":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            pages.append({"page_no": i, "text": text, "image_bytes": None, "mime": None})
        if not pages:
            pages.append({"page_no": 1, "text": "", "image_bytes": None, "mime": None})
        return pages

    # 图片：EXIF 方向归一化（§1.2 Pillow）
    image_bytes = content
    try:
        import io as _io

        from PIL import Image, ImageOps

        img = Image.open(_io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        out = _io.BytesIO()
        img_format = img.format or "JPEG"
        img.save(out, format=img_format)
        image_bytes = out.getvalue()
    except Exception:  # noqa: BLE001  (损坏图片按原样交给解析器)
        image_bytes = content
    pages.append(
        {
            "page_no": 1,
            "text": "",
            "image_bytes": image_bytes,
            "mime": document.mime_type,
        }
    )
    return pages


def _store_page_rows(db: Session, document: Document, pages: List[PageAsset]) -> None:
    """落 document_pages（unique(document_id,page_no)，重跑先删旧行）。

    注意：必须用 Core DELETE + flush 立即执行；ORM `db.delete()` 的 DELETE 会排在
    INSERT 之后，重复抽取同一文档时会撞 uq_document_pages_document_id_page_no
    （2026-10-21 真实环境实测）。
    """
    from sqlalchemy import delete

    from app.db.models import DocumentPage as DP

    db.execute(delete(DP).where(DP.document_id == document.id))
    db.flush()
    for p in pages:
        db.add(
            DP(
                id=new_id(),
                document_id=document.id,
                page_no=p["page_no"],
                image_key=document.file_key if len(pages) == 1 else None,
            )
        )


def _progress_reporter(job_id: str) -> Callable[..., None]:
    """阶段/进度上报器（独立 session，避免干扰主事务；同时续租，防被误回收）。

    节流：同一阶段进展变化或距上次写入超过 2s 才落库，避免高频写库。
    """
    from app.db.session import SessionLocal

    state = {"stage": "", "done": -1, "total": -1, "at": 0.0}
    stage_started = {"at": time.monotonic()}

    def report(stage: str, done: int = 0, total: int = 0, force: bool = False) -> None:
        now = time.monotonic()
        if stage != state["stage"]:
            stage_started["at"] = now
        same = stage == state["stage"] and done == state["done"] and total == state["total"]
        if same and not force and now - state["at"] < 2:
            return
        state.update({"stage": stage, "done": done, "total": total, "at": now})
        session = SessionLocal()
        try:
            job = session.get(ExtractionJob, job_id)
            if job is None or job.state not in ("running", "queued"):
                return
            job.stage = stage
            job.progress_done = max(0, int(done))
            job.progress_total = max(0, int(total))
            job.lease_until = datetime.now(timezone.utc) + timedelta(
                seconds=settings.job_lease_seconds
            )
            session.commit()
        except Exception:  # noqa: BLE001  进度上报失败不影响抽取
            session.rollback()
            logger.debug("pipeline.progress_failed job=%s stage=%s", job_id, stage)
        finally:
            session.close()

    return report


def _stage_elapsed_hint(stage: str) -> str:
    return {
        "loading": "正在读取原件…",
        "rendering": "正在把 PDF 渲染成图片…",
        "extracting": "正在逐页识别（AI 视觉）…",
        "mapping": "正在匹配指标字典…",
    }.get(stage, stage)


def process_job(db: Session, job_id: str) -> None:
    """worker 单任务处理：状态机前向 + 分类 + 解析链 + 映射 + 落库（§5.1）。"""
    job = db.get(ExtractionJob, job_id)
    if job is None or job.state in ("succeeded", "cancelled"):
        return
    document = db.get(Document, job.document_id)
    if document is None:
        job.state = "failed"
        job.error = "document missing"
        db.commit()
        return

    # 归档/已确认的文档不再处理
    if document.status in ("archived", "confirmed"):
        job.state = "cancelled"
        db.commit()
        return

    # pending（首次）/ extracting（重新抽取已提前置位）/ awaiting_review、parsed_no_data、
    # failed（重新抽取）→ 继续处理
    if document.status not in (
        "pending", "extracting", "awaiting_review", "parsed_no_data", "failed",
    ):
        logger.warning("pipeline.skip doc=%s status=%s", document.id, document.status)
        job.state = "cancelled"
        job.error = f"document status={document.status}"
        db.commit()
        return
    if document.status == "extracting" and job.state == "queued":
        # 重新抽取：状态已提前置为 extracting，这里只需把 job 置 running
        pass
    elif document.status != "extracting":
        assert_transition(document.status, "extracting")
        document.status = "extracting"
    job.state = "running"
    job.started_at = datetime.now(timezone.utc)
    job.worker_id = f"{socket.gethostname()}:{os.getpid()}"
    job.lease_until = datetime.now(timezone.utc) + timedelta(
        seconds=settings.job_lease_seconds
    )
    job.stage = "loading"
    job.progress_done = 0
    job.progress_total = 0
    db.commit()

    report = _progress_reporter(job.id)

    try:
        content = get_storage_client().get(document.file_key)
    except Exception as exc:  # noqa: BLE001
        _fail(db, document, job, f"原件读取失败：{exc}")
        return

    try:
        pages = build_pages(document, content)
    except Exception as exc:  # noqa: BLE001
        _fail(db, document, job, f"文件解析失败：{type(exc).__name__}: {exc}")
        return

    # ---- 出域脱敏（A-4/§6.2：抽取调用前对文本脱敏；图片 PII 走 LOCAL_MODEL_MODE）----
    try:
        from app.auth.deps import require_subject  # noqa: F401
        from app.db.models import Account, Subject
        from app.sanitizer.pipeline import sanitize_for_llm, subject_pii_bundle

        subject = db.get(Subject, document.subject_id)
        account = db.get(Account, document.account_id)
        if subject is not None and account is not None:
            names, relation_map, birthday = subject_pii_bundle(db, account, subject)
            for p in pages:
                if p.get("text"):
                    p["text"] = sanitize_for_llm(
                        p["text"], names=names,
                        relation_map=relation_map, birthday_iso=birthday,
                    ).text
    except Exception:  # noqa: BLE001
        logger.exception("pipeline.sanitize_skip doc=%s", document.id)

    text = "\n".join((p.get("text") or "") for p in pages)
    # 分类分流（AC-4.2）
    doc_type = classify_document(document.file_name, text)
    document.doc_type = doc_type
    document.page_count = len(pages)
    _store_page_rows(db, document, pages)
    db.commit()

    # AI 底座：指标语义对齐（未命中项由 AI 判定复用/新建标准指标）
    try:
        from app.llm.client import get_llm_client

        ai_llm = get_llm_client("extraction")
    except Exception:  # noqa: BLE001  AI 不可用不阻塞抽取
        ai_llm = None

    inp: ParseInput = {
        "document_id": document.id,
        "doc_type": doc_type,
        "filename": document.file_name,
        "pages": pages,
        "language_hint": "zh-CN",
        "llm_client": None,
        # 原始字节：表格解析（pdfplumber）需要；不解引用到日志/审计
        "raw_content": content,
    }
    # 页图延迟渲染（仅多页 PDF；单页图片已经是图）
    if document.kind == "PDF" and len(pages) > 0 and not any(
        p.get("image_bytes") for p in pages
    ):
        def _render():
            from app.preprocessing.pdf_render import render_pdf_pages

            report("rendering", 0, len(pages), force=True)
            rendered = render_pdf_pages(content)
            report("rendering", len(rendered), len(pages), force=True)
            return rendered

        inp["pdf_render"] = _render

    def _page_progress(done: int, total: int) -> None:
        report("extracting", done, total)

    inp["progress"] = _page_progress
    prefer_rule = job.attempt > 0  # 重试优先确定性/本地链路（§6.6）

    # ---- 解析策略（§21）：按文档特征与配置决定主路径 ----
    # 用户要求「LLM 可用时优先视觉识别」；策略由 extract.parser_strategy 控制，
    # 默认 vlm_first；table_first 可一条配置回滚到原行为。
    from app.config import resolver
    from app.preprocessing.parsers import (
        build_doc_profile,
        first_available_name,
        select_parser_chain_by_strategy,
    )

    strategy = resolver.get_str(db, "extract.parser_strategy", "vlm_first")
    vlm_max_pages = resolver.get_int(db, "extract.vlm_always_pdf_max_pages", 6)
    vision_enabled = resolver.get_bool(db, "vision.enabled", True)

    # 页面文本层与表格数：PDF 才有；图片类默认无文本层
    text_layer = bool(inp.get("raw_content"))
    table_count = int(inp.get("table_count") or 0)
    profile = build_doc_profile(
        kind=document.kind or "PDF",
        page_count=len(pages) or 1,
        has_text_layer=text_layer,
        table_count=table_count,
    )
    # vision.enabled=false 时视为视觉不可用（配置层降级，无需改解析器实现）
    if not vision_enabled:
        from dataclasses import replace as _replace

        profile = _replace(profile, vlm_available=False)

    logger.info(
        "pipeline.strategy strategy=%s primary=%s kind=%s pages=%s textLayer=%s tables=%s vlm=%s ocr=%s",
        strategy,
        first_available_name(
            select_parser_chain_by_strategy(
                profile, strategy=strategy, prefer_rule=prefer_rule,
                vlm_max_pages=vlm_max_pages,
            )
        ),
        profile.kind,
        profile.page_count,
        profile.has_text_layer,
        profile.table_count,
        profile.vlm_available,
        profile.ocr_available,
    )

    # ---- 解析链：按策略尝试可用解析器 ----
    # 表格解析是确定性的且最快，但可能只覆盖部分页面（2021_0907.pdf 实测仅 55/127 项）；
    # 此时继续跑视觉识别并融合，兼顾准确率与覆盖率。
    from app.preprocessing.merger import table_coverage_sufficient

    result: Optional[ParserResult] = None
    supplement: Optional[ParserResult] = None
    table_result: Optional[ParserResult] = None
    errors: List[str] = []
    chain = select_parser_chain_by_strategy(
        profile,
        strategy=strategy,
        prefer_rule=prefer_rule,
        vlm_max_pages=vlm_max_pages,
    )
    for parser in chain:
        report("extracting", 0, len(pages), force=True)
        res = parser.parse(inp)
        if res.get("success"):
            if parser.name == "table" and not table_coverage_sufficient(
                len(res.get("observations") or []), len(pages)
            ):
                # 表格覆盖不足：先记住结果，继续找视觉/OCR 解析器补漏
                table_result = res
                continue
            result = res
            break
        errors.append(f"{parser.name}: {res.get('error')}")
        # LLM 恰好刚恢复：刷新可用性（简单起见继续下一解析器）

    if result is None and table_result is not None:
        # 视觉/OCR 都没跑通，但表格仍有结果 → 用表格结果兜底（比整份失败好）
        result = table_result
        table_result = None

    if result is None:
        _fail(db, document, job, "；".join(errors) or "无可用解析器")
        return

    # 表格覆盖不足且有可用的视觉/OCR 结果 → 以表格为基准融合补漏
    if table_result is not None:
        supplement = result
        result = table_result

    report("mapping", len(pages), len(pages), force=True)

    from app.preprocessing.extractor import validate_derived_record

    def _normalized(res: ParserResult) -> dict:
        return validate_derived_record(
            {
                "report_date": res.get("report_date"),
                "doc_type_suggest": res.get("doc_type") or doc_type,
                "events": res.get("events") or [],
                "observations": res.get("observations") or [],
                "mock": res.get("mock", False),
            },
            doc_type,
        )

    payload = _normalized(result)
    if supplement is not None:
        from app.preprocessing.merger import merge_payloads

        # 先各自做字典映射再融合：映射后同名别名会归到同一 code，
        # 去重才能把「体重指数 / 身体质量指数」识别为同一指标。
        payload = map_payload(
            db,
            payload,
            subject_id=document.subject_id,
            document_id=document.id,
            llm=ai_llm,
            doc_type=doc_type,
        )
        supplement_payload = map_payload(
            db,
            _normalized(supplement),
            subject_id=document.subject_id,
            document_id=document.id,
            llm=ai_llm,
            doc_type=doc_type,
        )
        payload = merge_payloads(
            payload,
            supplement_payload,
            primary_parser=result.get("parser_name") or "table",
            secondary_parser=supplement.get("parser_name") or "vlm",
        )
        logger.info(
            "extraction.merged doc=%s primary=%s secondary=%s obs=%s added=%s",
            document.id,
            result.get("parser_name"),
            supplement.get("parser_name"),
            len(payload["observations"]),
            payload.get("supplemented"),
        )
    else:
        # 字典映射 + AI 指标对齐 + E-3 未命中队列
        payload = map_payload(
            db,
            payload,
            subject_id=document.subject_id,
            document_id=document.id,
            llm=ai_llm,
            doc_type=doc_type,
        )

    # 同文档同日同名指标去重（融合会带入重复项，用户看到互相矛盾的数值）
    from app.preprocessing.merger import dedupe_observations

    payload["observations"], dropped_dupes = dedupe_observations(
        payload.get("observations") or []
    )
    if dropped_dupes:
        payload["deduped"] = dropped_dupes
        logger.info(
            "extraction.dedup doc=%s dropped=%s kept=%s",
            document.id, dropped_dupes, len(payload["observations"]),
        )

    # 排版噪声（T1/年0/每3/页码/联系方式）不该出现在待确认列表里：
    # 用户没有义务逐条拒绝"表格行标"，它们不是检验项目。
    from app.preprocessing.indicator_aligner import is_noise_text

    kept: list = []
    dropped_noise: list = []
    for obs in payload.get("observations") or []:
        if is_noise_text(str(obs.get("raw_name") or "")) and not obs.get("code"):
            dropped_noise.append(obs.get("raw_name"))
            continue
        kept.append(obs)
    if dropped_noise:
        payload["observations"] = kept
        payload["noise_filtered"] = len(dropped_noise)
        logger.info(
            "extraction.noise_filtered doc=%s count=%s samples=%s",
            document.id, len(dropped_noise), dropped_noise[:5],
        )
    # 保留页级诊断（部分页失败时前端可提示“已识别 18/20 页”）
    page_errors = list(result.get("page_errors") or [])
    if supplement is not None:
        page_errors += list(supplement.get("page_errors") or [])
    if page_errors:
        payload["page_errors"] = list(dict.fromkeys(page_errors))[:10]
    page_count = result.get("page_count") or (
        supplement.get("page_count") if supplement is not None else None
    )
    if page_count:
        payload["page_count"] = int(page_count)
    # 记录实际使用的解析器（表格+视觉融合时两者都记）
    parsers_used = [result.get("parser_name") or "unknown"]
    if supplement is not None and supplement.get("parser_name"):
        parsers_used.append(supplement["parser_name"])
    if len(parsers_used) > 1:
        payload["parsers_used"] = parsers_used

    has_content = bool(payload["events"] or payload["observations"])
    if not has_content:
        # 已解析但无事件（§9.2 parsed_no_data）
        assert_transition(document.status, "parsed_no_data")
        document.status = "parsed_no_data"
        job.state = "succeeded"
        job.parser = result["parser_name"]
        job.stage = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("extraction.done doc=%s parser=%s result=no_data",
                    document.id, result["parser_name"])
        return

    # 幂等：同文档取当前最大版本 +1（B-4 UNIQUE(document_id, version)）
    max_version = db.scalar(
        select(func.max(DerivedRecord.version)).where(
            DerivedRecord.document_id == document.id
        )
    ) or 0
    confs = [
        float(o.get("confidence") or 0)
        for o in payload.get("observations") or []
    ]
    overall = round(sum(confs) / len(confs), 3) if confs else None

    dr = DerivedRecord(
        id=new_id(),
        document_id=document.id,
        subject_id=document.subject_id,
        version=int(max_version) + 1,
        payload=payload,
        parser=result["parser_name"],
        overall_confidence=overall,
        source_text=(result.get("source_text") or "")[:200000],
        created_by="system",
    )
    db.add(dr)

    assert_transition(document.status, "awaiting_review")
    document.status = "awaiting_review"
    job.state = "succeeded"
    job.parser = result["parser_name"]
    job.stage = "done"
    job.progress_done = job.progress_total = int(result.get("page_count") or len(pages))
    job.finished_at = datetime.now(timezone.utc)
    db.flush()
    # 清理过旧版本（保留最近 N 版，已确认版不删），防止反复重抽无限堆积
    pruned = prune_derived_records(db, document.id)
    if pruned:
        logger.info("derived_records.pruned doc=%s count=%s", document.id, pruned)
    db.commit()

    elapsed_ms = 0.0
    started = job.started_at
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    logger.info(
        "extraction.done doc=%s parser=%s version=%s obs=%s events=%s elapsedMs=%.0f",
        document.id, result["parser_name"], dr.version,
        len(payload["observations"]), len(payload["events"]), elapsed_ms,
    )


def _fail(db: Session, document: Document, job: ExtractionJob, error: str) -> None:
    """解析失败（E-1）：doc→failed + failure_reason；job→failed。"""
    if document.status == "extracting":
        assert_transition(document.status, "failed")
        document.status = "failed"
    document.failure_reason = error[:2000]
    job.state = "failed"
    job.error = error[:2000]
    job.stage = "failed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    logger.warning("extraction.failed doc=%s err=%s", document.id, error)
