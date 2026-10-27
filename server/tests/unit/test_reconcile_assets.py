"""R1 出口对账脚本的纯函数测试（离线，不连 DB/MinIO）。

`reconcile_assets.py` 顶层只依赖标准库（app 导入在 main() 内），
因此可直接加载模块测 classify_asset / Report.ok，无需容器。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "reconcile_assets.py"


@pytest.fixture(scope="module")
def rec():
    spec = importlib.util.spec_from_file_location("reconcile_assets", _MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    # 必须注册进 sys.modules：否则 @dataclass 实例化时按 cls.__module__ 反查
    # sys.modules 会拿到 None，触发 "'NoneType' object has no attribute '__dict__'"。
    import sys

    sys.modules["reconcile_assets"] = module
    spec.loader.exec_module(module)
    return module


def test_load_path_exists():
    assert _MOD_PATH.exists(), _MOD_PATH


def test_classify_asset_fresh_and_stale(rec):
    assert rec.classify_asset("abc", "abc") == "fresh"
    assert rec.classify_asset("abc", "def") == "stale"


def test_classify_asset_unknown_when_missing(rec):
    # 历史行无 source_hash → unknown，不武断判过期
    assert rec.classify_asset(None, "abc") == "unknown"
    assert rec.classify_asset("abc", None) == "unknown"
    assert rec.classify_asset("", "abc") == "unknown"


def test_report_ok_flags_red_lines(rec):
    ok = rec.Report()
    assert ok.ok is True
    ok.originals_missing = ["d1"]
    assert ok.ok is False

    ok2 = rec.Report()
    ok2.assets_orphan_missing_doc = 1
    assert ok2.ok is False

    ok3 = rec.Report()
    ok3.errors = ["boom"]
    assert ok3.ok is False


def test_report_warns_but_not_fail_on_soft_deleted_orphans(rec):
    # 指向软删文档的资产可接受（不 FAIL），只是计数
    r = rec.Report(assets_orphan_soft_deleted_doc=5)
    assert r.ok is True
