"""评测口径冻结（R0 T002）。

目的：让"跑分结果"可复现、可解释，避免用调参集冒充留出集，也避免用不完整
的评测集输出"通过"结论。

冻结的规则：
1. **K3 必须完整**：判���数 < 20 或存在未判定问题 → 只报告进度，**不输出通过率**；
2. **K1/K2 需报告精确率与误增**：只报召回会把"抽出一堆垃圾字段"评为满分；
3. **按文档宏平均**：单份报告的大字段量不得主导整体指标；
4. **分层结果**：有 `docType`/`hospital` 标注时按层报告，避免总体值掩盖某类崩塌；
5. **版本清单**：数据集/标注/评分器/预测版本必须可追溯；
6. **selftest 必须覆盖新口径**：防止为涨分放松安全阀。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_EVALS / "scripts"))

import score_kpi as sk  # noqa: E402


# --------------------------------------------------------------------------
# K3 完整性门槛
# --------------------------------------------------------------------------


def _q(i: int, verdict) -> dict:
    return {"id": f"q{i:02d}", "question": "问题", "verdict_manual": verdict}


def test_k3_incomplete_set_reports_no_rate():
    """判定的题量不足 20 → 不得输出通过率与结论。"""
    rows = [_q(i, "pass") for i in range(1, 6)]
    out = sk.score_k3(rows)
    assert "rate" not in out
    assert "verdict" not in out
    assert out["judged"] == 5
    assert out["required"] == sk.MIN_K3_QUESTIONS
    assert out["complete"] is False


def test_k3_unjudged_question_blocks_verdict():
    """存在未判定问题 → 不输出通过率（否则可挑选已通过的子集刷分）。"""
    rows = [_q(i, "pass") for i in range(1, 21)]
    rows.append(_q(21, None))
    out = sk.score_k3(rows)
    assert "rate" not in out
    assert out["complete"] is False
    assert out["unjudged"] == 1


def test_k3_complete_set_reports_rate():
    """20 题全部判定 → 输出通过率与结论。"""
    rows = [_q(i, "pass" if i <= 18 else "fail") for i in range(1, 21)]
    out = sk.score_k3(rows)
    assert out["complete"] is True
    assert out["judged"] == 20
    assert out["rate"] == pytest.approx(0.9, abs=1e-3)
    assert out["verdict"]["K3"] == "GO"


def test_k3_threshold_is_enforced():
    """低于阈值 → NO-GO。"""
    rows = [_q(i, "pass" if i <= 15 else "fail") for i in range(1, 21)]
    out = sk.score_k3(rows)
    assert out["verdict"]["K3"] == "NO-GO"


# --------------------------------------------------------------------------
# K1/K2 精确率、宏平均、分层
# --------------------------------------------------------------------------


def _ann(name, value="1.0", unit="U", date="2026-01-01", cls="primary", **extra):
    field = {
        "name": name,
        "value": value,
        "unit": unit,
        "date": date,
        "abnormal": False,
        "kpiClass": cls,
    }
    field.update(extra)
    return {"file": extra.get("file", "files/a.pdf"), "fields": [field], **{
        k: v for k, v in extra.items() if k in ("docType", "hospital", "file")
    }}


def test_k1k2_reports_precision_and_macro(tmp_path):
    """必须同时给出召回与精确率，以及按文档宏平均。"""
    preds = tmp_path / "predictions"
    preds.mkdir()
    (preds / "a.json").write_text(
        json.dumps({"file": "files/a.pdf", "fields": [
            {"name": "血红蛋白", "value": "1.0", "unit": "U", "date": "2026-01-01"},
            {"name": "多余指标", "value": "9", "unit": "U", "date": "2026-01-01"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    anns = [_ann("血红蛋白")]
    res = sk.score_k1k2(anns, preds, include_classes=("primary",))
    assert res["K1_field_accuracy"][0] == 1
    # 精确率：预测字段 2 个，命中 1 个
    assert res["K1_precision"][0] == 1
    assert res["K1_precision"][1] == 2
    # 宏平均存在且为文档级
    assert "K1_macro" in res


def test_k1k2_stratified_by_doctype(tmp_path):
    """有 docType 标注时按类型分层报告。"""
    preds = tmp_path / "predictions"
    preds.mkdir()
    for stem in ("a", "b"):
        (preds / f"{stem}.json").write_text(
            json.dumps({"file": f"files/{stem}.pdf", "fields": [
                {"name": "血红蛋白", "value": "1.0", "unit": "U", "date": "2026-01-01"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
    anns = [
        {"file": "files/a.pdf", "docType": "lab_report", "fields": [
            {"name": "血红蛋白", "value": "1.0", "unit": "U", "date": "2026-01-01",
             "abnormal": False, "kpiClass": "primary"}]},
        {"file": "files/b.pdf", "docType": "imaging_report", "fields": [
            {"name": "血红蛋白", "value": "1.0", "unit": "U", "date": "2026-01-01",
             "abnormal": False, "kpiClass": "primary"}]},
    ]
    res = sk.score_k1k2(anns, preds, include_classes=("primary",))
    strata = res["stratified_by_docType"]
    assert set(strata) == {"lab_report", "imaging_report"}
    assert strata["lab_report"]["K1_field_accuracy"] == (1, 1)


def test_k1k2_macro_not_dominated_by_large_document(tmp_path):
    """宏平均按文档计，避免单份大报告主导整体指标。"""
    preds = tmp_path / "predictions"
    preds.mkdir()
    # 大文档：20 个标注项，只命中 1 个
    big_fields = [{"name": f"指标{i}", "value": "1", "unit": "U", "date": "2026-01-01"}
                  for i in range(20)]
    (preds / "big.json").write_text(
        json.dumps({"file": "files/big.pdf", "fields": big_fields[:1]}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 小文档：1 个标注项，全命中
    (preds / "small.json").write_text(
        json.dumps({"file": "files/small.pdf", "fields": [
            {"name": "甲", "value": "1", "unit": "U", "date": "2026-01-01"}]},
            ensure_ascii=False),
        encoding="utf-8",
    )
    anns = [
        {"file": "files/big.pdf", "fields": [
            {"name": f"指标{i}", "value": "1", "unit": "U", "date": "2026-01-01",
             "abnormal": False, "kpiClass": "primary"} for i in range(20)]},
        {"file": "files/small.pdf", "fields": [
            {"name": "甲", "value": "1", "unit": "U", "date": "2026-01-01",
             "abnormal": False, "kpiClass": "primary"}]},
    ]
    res = sk.score_k1k2(anns, preds, include_classes=("primary",))
    # micro：big 文档 20 项命中 1，small 文档 1 项命中 1 → 2/21
    assert res["K1_field_accuracy"] == (2, 21)
    # macro：big 文档 1/20=0.05，small 文档 1/1=1.0 → 0.525
    assert res["K1_macro"] == pytest.approx(0.525, abs=0.01)


# --------------------------------------------------------------------------
# 版本清单
# --------------------------------------------------------------------------


def test_manifest_reports_versions(tmp_path):
    """结果必须带版本信息，便于复现。"""
    manifest = {
        "dataset_version": "2026-10-26",
        "annotation_version": "v2",
        "scorer_version": "v3",
        "prediction_version": "p1",
        "split": "holdout",
        "sample_counts": {"documents": 38, "primary_fields": 282},
    }
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    versions = sk.load_manifest(tmp_path)
    assert versions["dataset_version"] == "2026-10-26"
    assert versions["split"] == "holdout"
    assert versions["sample_counts"]["documents"] == 38


def test_manifest_missing_is_reported_not_fatal(tmp_path):
    """缺 manifest 时明确报告未提供（不得静默当作已冻结）。"""
    versions = sk.load_manifest(tmp_path)
    assert versions.get("present") is False


# --------------------------------------------------------------------------
# selftest 覆盖新口径
# --------------------------------------------------------------------------


def test_selftest_covers_new_gates():
    """selftest 必须包含 K3 完整性与精确率口径的断言。"""
    import inspect

    src = inspect.getsource(sk.selftest)
    assert "MIN_K3_QUESTIONS" in src
    assert "K1_precision" in src or "precision" in src
