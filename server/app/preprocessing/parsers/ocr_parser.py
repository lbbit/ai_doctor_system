"""OCR 兜底解析器：图像预处理 → OCR 文本 → 文本 LLM 结构化（或规则）。

为什么需要：
- 视觉模型（VLM）对表格线密集、字号小的化验单偶尔会整页漏读或超时；
- 扫描件/拍照件没有文本层，规则解析无从下手；
- OCR 给出稳定的文本，再由**文本**模型结构化，成本低、可重试、且能落
  `source_text` 供引用回链（A-10）。

可用性：`ELIGIBLE_OCR_PROVIDERS` 中任一 provider 可用即启用。默认实现支持
- rapidocr（onnxruntime，纯 pip，离线）
- tesseract（系统二进制 + pytesseract，语言包 chi_sim+eng）
两者都没有时 available()=False，链路自动跳过，不影响既有能力。
"""

import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.preprocessing.noise import is_noise_measurement
from app.preprocessing.parser_base import ParseInput, ParserResult
from app.preprocessing.parsers.rule_parser import (
    _extract_date,
    _parse_flag,
    _parse_ref,
    _parse_value,
)

logger = logging.getLogger("app.parsers.ocr")

# OCR 行：指标名 + 数值 + 单位 + 参考区间（与规则解析互补，容忍 OCR 空格噪声）
_OCR_OBS = re.compile(
    r"^(?P<name>[一-龥A-Za-z][一-龥A-Za-z0-9()（）/·%\-、\s]{1,30}?)\s*"
    r"(?P<value>[<>≤≥]?\s*-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[a-zA-Zμ%×\^/\d\.]{0,16})?\s*"
    r"(?P<rest>.*)$"
)
_REF_NUM = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:-{1,2}|~|～|—|–|至)\s*(-?\d+(?:\.\d+)?)"
)


def _ocr_line_to_obs(line: str, report_date: Optional[str]) -> Optional[Dict[str, Any]]:
    text = line.strip()
    if len(text) < 3 or len(text) > 200:
        return None
    # 先剥掉左侧的项目编号 / 序号
    text = re.sub(r"^[\d一二三四五六七八九十]+[.、)\]]?\s*", "", text)
    match = _OCR_OBS.match(text)
    if not match:
        return None
    name = match.group("name").strip()
    if is_noise_measurement(name):
        return None
    value = _parse_value(match.group("value"))
    if value is None:
        return None
    rest = (match.group("rest") or "").strip()
    unit = (match.group("unit") or "").strip() or None
    low, high, ref_text = _parse_ref(rest)
    value_num = value if isinstance(value, (int, float)) else None
    flag = _parse_flag(rest, value_num, low, high)
    return {
        "raw_name": name[:128],
        "value": value,
        "unit": unit,
        "ref_low": low,
        "ref_high": high,
        "ref_text": ref_text,
        "abnormal_flag": flag,
        "occurred_at": report_date,
        # OCR 文本再解析的置信度低于原生文本层
        "confidence": 0.7 if (low is not None or ref_text) else 0.55,
        "bbox": None,
        "status": "proposed",
        "code": None,
        "dictionary_id": None,
        "display_name": name,
    }


def ocr_text_to_observations(
    text: str, *, report_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """OCR 全文 → observations（纯函数，便于单测）。"""
    observations: List[Dict[str, Any]] = []
    seen: set = set()
    for line in text.splitlines():
        obs = _ocr_line_to_obs(line, report_date)
        if obs is None:
            continue
        key = (obs["raw_name"], str(obs["value"]), obs["unit"])
        if key in seen:
            continue
        seen.add(key)
        observations.append(obs)
    return observations


class OcrParser:
    """OCR 兜底：先 try OCR 文本 → 规则抽取；文本模型可用时再做结构化增强。"""

    name = "ocr"
    supported_doc_types = {"lab_report", "imaging_report", "visit_record", "other"}
    min_observations = 2

    def __init__(self) -> None:
        self._provider: Optional[str] = None
        self._probed = False

    # ---- provider 探测 ----
    def _detect_provider(self) -> Optional[str]:
        if self._probed:
            return self._provider
        self._probed = True
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401

            self._provider = "rapidocr"
            return self._provider
        except Exception:  # noqa: BLE001
            pass
        try:
            import pytesseract
            from PIL import Image  # noqa: F401

            pytesseract.get_tesseract_version()
            self._provider = "tesseract"
        except Exception:  # noqa: BLE001
            self._provider = None
        return self._provider

    def available(self) -> bool:
        return self._detect_provider() is not None

    # ---- OCR 执行 ----
    def _preprocess(self, image_bytes: bytes) -> bytes:
        """灰度 + 自动对比度 + 长边限制：提升小字号识别率、控制耗时。"""
        try:
            from PIL import Image, ImageOps

            img = Image.open(io.BytesIO(image_bytes))
            img = ImageOps.exif_transpose(img)
            img = img.convert("L")
            img = ImageOps.autocontrast(img, cutoff=1)
            max_side = 2400
            if max(img.size) > max_side:
                ratio = max_side / max(img.size)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)))
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue()
        except Exception:  # noqa: BLE001
            return image_bytes

    def _ocr_image(self, image_bytes: bytes) -> str:
        provider = self._detect_provider()
        prepared = self._preprocess(image_bytes)
        if provider == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
            result, _ = engine(prepared)
            if not result:
                return ""
            # RapidOCR 返回 [[box, text, score], ...]；按 y 坐标还原阅读顺序
            rows: List[Tuple[float, float, str]] = []
            for box, text, _score in result:
                ys = [point[1] for point in box]
                xs = [point[0] for point in box]
                rows.append((min(ys), min(xs), str(text)))
            rows.sort(key=lambda item: (round(item[0] / 12), item[1]))
            return "\n".join(text for _y, _x, text in rows)

        if provider == "tesseract":
            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(prepared))
            return pytesseract.image_to_string(img, lang="chi_sim+eng")

        return ""

    def parse(self, inp: ParseInput) -> ParserResult:
        pages = inp.get("pages") or []
        doc_type = inp.get("doc_type") or "other"
        if not self.available():
            return _fail("OCR 引擎不可用（未安装 rapidocr/pytesseract）")

        texts: List[str] = []
        errors: List[str] = []
        for page in pages:
            image_bytes = page.get("image_bytes")
            page_text = (page.get("text") or "").strip()
            if page_text:
                texts.append(page_text)
                continue
            if not image_bytes:
                continue
            try:
                texts.append(self._ocr_image(image_bytes))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"第{page.get('page_no')}页 OCR 失败：{type(exc).__name__}")
                logger.warning(
                    "ocr.page_failed doc=%s page=%s err=%s",
                    inp.get("document_id"), page.get("page_no"), exc,
                )

        full_text = "\n".join(t for t in texts if t).strip()
        if not full_text:
            return _fail("；".join(errors) or "OCR 未识别到文本")

        report_date = _extract_date(full_text)
        observations = ocr_text_to_observations(full_text, report_date=report_date)

        # 规则抽不到时，退一步用文本模型把 OCR 文本结构化成 JSON（不重跑视觉）
        if len(observations) < self.min_observations:
            structured = self._structure_with_llm(inp, full_text, doc_type)
            if structured is not None:
                return structured

        if not observations:
            return _fail("；".join(errors) or "OCR 文本未解析出指标")

        return {
            "parser_name": self.name,
            "report_date": report_date,
            "doc_type": doc_type,
            "events": [],
            "observations": observations,
            "source_text": full_text[:200000],
            "success": True,
            "error": None,
        }

    def _structure_with_llm(
        self, inp: ParseInput, text: str, doc_type: str
    ) -> Optional[ParserResult]:
        try:
            from app.llm.client import get_llm_client
            from app.llm.extraction import extract_report_payload

            llm = inp.get("llm_client") or get_llm_client("extraction")
            if not llm.available():
                return None
            raw = extract_report_payload(llm, content=text, doc_type=doc_type)
            observations = raw.get("observations") or []
            if len(observations) < self.min_observations:
                return None
            from app.preprocessing.extractor import validate_derived_record

            payload = validate_derived_record(raw, doc_type)
            return {
                "parser_name": self.name,
                "report_date": payload.get("report_date"),
                "doc_type": payload.get("doc_type_suggest") or doc_type,
                "events": payload.get("events") or [],
                "observations": payload.get("observations") or [],
                "source_text": text[:200000],
                "success": True,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.info("ocr.llm_structure_skip doc=%s err=%s", inp.get("document_id"), exc)
            return None


def _fail(error: str) -> ParserResult:
    return {
        "parser_name": "ocr",
        "report_date": None,
        "doc_type": "",
        "events": [],
        "observations": [],
        "source_text": "",
        "success": False,
        "error": error,
    }
