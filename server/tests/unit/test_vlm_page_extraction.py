"""VLM 分页抽取与合并（PDF 逐页 → VLM → 去重合并）。

缺陷背景：整份 20 页 PDF 一次性送 VLM 会超过 60s 网关超时（实测 121s 失败），
降级到规则解析后产出噪声草稿。改为逐页图片抽取（实测单页 5.6s）。
"""

from app.preprocessing.parsers.vlm_parser import VlmParser


class FakePageLLM:
    """按传入图片内容分派不同页的 JSON（并发安全，不依赖调用顺序）。"""

    def __init__(self, by_image: dict) -> None:
        self.by_image = by_image
        self.calls = []
        self.model = "fake-text-model"
        self.vlm_model = "fake-vlm-model"

    def available(self) -> bool:
        return True

    def image_to_data_url(self, image_bytes, mime) -> str:
        return f"data:{mime};base64,{image_bytes.decode()}"

    def chat(self, messages, **kwargs) -> str:
        self.calls.append(messages)
        blob = str(messages)
        for tag, body in self.by_image.items():
            if f"base64,{tag}" in blob:
                return body
        raise AssertionError(f"无法识别的页内容: {blob[:200]}")


def _inp(pages):
    return {
        "document_id": "doc-1",
        "doc_type": "lab_report",
        "filename": "report.pdf",
        "pages": pages,
        "language_hint": "zh-CN",
        "llm_client": None,
    }


PAGE_A = """
{"report_date": "2020-09-06", "doc_type_suggest": "lab_report", "events": [],
 "observations": [
   {"raw_name": "体重指数", "value": 32.4, "unit": null, "ref_low": 18.5, "ref_high": 23.99, "confidence": 0.95},
   {"raw_name": "收缩压", "value": 116, "unit": "mmHg", "ref_low": 90, "ref_high": 139, "confidence": 0.95}
 ]}
"""

PAGE_B = """
{"report_date": "2020-09-06", "doc_type_suggest": "lab_report", "events": [],
 "observations": [
   {"raw_name": "体重指数", "value": 32.4, "unit": null, "ref_low": 18.5, "ref_high": 23.99, "confidence": 0.9},
   {"raw_name": "总胆固醇", "value": 6.21, "unit": "mmol/L", "ref_low": 2.8, "ref_high": 5.2, "confidence": 0.93}
 ]}
"""


def test_pdf_pages_extracted_per_page_and_merged(monkeypatch):
    llm = FakePageLLM({"img-1": PAGE_A, "img-2": PAGE_B})
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda *a, **k: llm)

    progress = []
    inp = _inp(
        [
            {"page_no": 1, "image_bytes": b"img-1", "mime": "image/png", "text": "page one"},
            {"page_no": 2, "image_bytes": b"img-2", "mime": "image/png", "text": "page two"},
        ]
    )
    inp["progress"] = lambda done, total: progress.append((done, total))

    result = VlmParser().parse(inp)

    assert result["success"] is True
    assert len(llm.calls) == 2, "每页一次调用（避免整份超时）"
    names = [o["raw_name"] for o in result["observations"]]
    assert names == ["体重指数", "收缩压", "总胆固醇"], names
    assert result["report_date"] == "2020-09-06"
    assert progress[-1] == (2, 2), progress


def test_single_page_failure_keeps_other_pages(monkeypatch):
    class PartiallyBrokenLLM(FakePageLLM):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            blob = str(messages)
            if "base64,img-1" in blob:
                raise RuntimeError("网关 504")
            return PAGE_B

    llm = PartiallyBrokenLLM({"img-1": PAGE_A, "img-2": PAGE_B})
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda *a, **k: llm)

    result = VlmParser().parse(
        _inp(
            [
                {"page_no": 1, "image_bytes": b"img-1", "mime": "image/png", "text": ""},
                {"page_no": 2, "image_bytes": b"img-2", "mime": "image/png", "text": ""},
            ]
        )
    )
    assert result["success"] is True
    names = [o["raw_name"] for o in result["observations"]]
    assert "总胆固醇" in names
    assert result.get("page_errors"), "失败的页需留下诊断信息"


def test_all_pages_failed_reports_failure(monkeypatch):
    class BrokenLLM(FakePageLLM):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            raise RuntimeError("网关 504")

    llm = BrokenLLM({"img-1": PAGE_A})
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda *a, **k: llm)

    result = VlmParser().parse(
        _inp([{"page_no": 1, "image_bytes": b"img-1", "mime": "image/png", "text": ""}])
    )
    assert result["success"] is False
    assert "504" in (result.get("error") or "")
