#!/usr/bin/env python3
"""标注文件体检脚本（K1/K2 前置校验，只读不修改）。

用途：在跑分前发现标注的低级错误，避免"标注错 → 跑分假失败"。

检查项：
1. JSONL 每行可解析、必需键齐全
2. file 路径指向的样本文件真实存在
3. 文件名与 files/ 目录一一对应（无漏标、无孤儿）
4. fields 非空；name/value 必填；unit/date 存在
5. date 为 YYYY-MM-DD 且是合法日期
6. value 为可解析数字（或明确的阴性/阳性等定性值）
7. abnormal 为布尔
8. 同一文件内 name 不重复（重复会导致字典覆盖 → K1 分母虚高）
9. 重复样本文件（同名不同扩展名）提示
10. 单位可疑写法提示（大小写/空格/异体符号）

用法：
    python scripts/check_annotations.py --dir dataset/kpi_k1k2
    python scripts/check_annotations.py --dir dataset/kpi_k1k2 --strict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 定性结果允许值（这些不是数字，但临床上是合法录入）
QUALITATIVE_OK = {
    "阴性", "阳性", "弱阳性", "可疑", "未检出", "检出", "正常", "异常",
    "negative", "positive", "neg", "pos", "+", "-", "++", "+++", "++++",
    "未见异常", "无", "有",
}

# 常见单位写法问题（key 为规范化后形式，value 为建议）
UNIT_HINTS = {
    "g/l": "大小写不一致，建议统一为 g/L",
    "mg/dl": "大小写不一致，建议统一为 mg/dL",
    "mmol/l": "大小写不一致，建议统一为 mmol/L",
    "umol/l": "建议统一为 μmol/L（希腊字母）或确认系统口径",
    "10^9/l": "建议确认系统口径（10^9/L 或 ×10⁹/L）",
    "10*9/l": "常见笔误，应为 10^9/L",
}


class Issue:
    def __init__(self, level: str, where: str, message: str):
        self.level = level  # error | warn | info
        self.where = where
        self.message = message

    def __str__(self) -> str:
        tag = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}[self.level]
        return f"[{tag}] {self.where}: {self.message}"


def _is_numberish(value: str) -> bool:
    text = str(value).strip()
    if text in QUALITATIVE_OK:
        return True
    try:
        float(text)
        return True
    except ValueError:
        pass
    # 形如 "<0.01" ">1000" "0.5-1.0"
    if re.fullmatch(r"[<>≤≥]?\s*-?\d+(\.\d+)?", text):
        return True
    if re.fullmatch(r"-?\d+(\.\d+)?\s*[-~—]\s*-?\d+(\.\d+)?", text):
        return True
    return False


def check(dir_path: Path) -> tuple[list[Issue], dict]:
    issues: list[Issue] = []
    ann_file = dir_path / "annotations.jsonl"
    files_dir = dir_path / "files"

    if not ann_file.exists():
        issues.append(Issue("error", str(ann_file), "标注文件不存在"))
        return issues, {}

    # ---- 1. 逐行解析 ----
    rows: list[dict] = []
    for lineno, line in enumerate(
        ann_file.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            issues.append(Issue("warn", f"line {lineno}", "空行（建议删除）"))
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(Issue("error", f"line {lineno}", f"JSON 解析失败：{exc}"))
            continue
        if not isinstance(obj, dict):
            issues.append(Issue("error", f"line {lineno}", "该行不是 JSON 对象"))
            continue
        rows.append({"__lineno": lineno, **obj})

    # ---- 2. 文件存在性与一一对应 ----
    annotated_files: set[str] = set()
    for row in rows:
        where = f"line {row['__lineno']}"
        fname = row.get("file")
        if not fname:
            issues.append(Issue("error", where, "缺少 file 字段"))
            continue
        rel = fname.replace("\\", "/")
        if not rel.startswith("files/"):
            issues.append(
                Issue("warn", where, f"file 路径建议以 files/ 开头（当前 {fname}）")
            )
        target = dir_path / rel
        if not target.exists():
            issues.append(Issue("error", where, f"样本文件不存在：{rel}"))
        if rel in annotated_files:
            issues.append(Issue("error", where, f"重复标注同一文件：{rel}"))
        annotated_files.add(rel)

        # 必需键
        for key in ("subject", "fields"):
            if key not in row:
                issues.append(Issue("error", where, f"缺少必需键 {key}"))

        fields = row.get("fields")
        if not isinstance(fields, list) or not fields:
            issues.append(Issue("error", where, "fields 必须是非空数组"))
            continue

        # 字段级检查
        seen_names: Counter[str] = Counter()
        for idx, field in enumerate(fields):
            fw = f"{where} fields[{idx}]"
            if not isinstance(field, dict):
                issues.append(Issue("error", fw, "字段不是对象"))
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                issues.append(Issue("error", fw, "缺少 name"))
            else:
                seen_names[name] += 1

            value = field.get("value")
            if value is None or str(value).strip() == "":
                issues.append(Issue("error", fw, f"{name or '?'} 缺少 value"))
            elif not _is_numberish(value):
                issues.append(
                    Issue(
                        "warn",
                        fw,
                        f"{name} 的 value 非数字也非已知定性值：{value!r}",
                    )
                )

            unit = field.get("unit")
            if unit is None or str(unit).strip() == "":
                issues.append(
                    Issue("warn", fw, f"{name or '?'} 缺少 unit（会导致 K1 判错）")
                )
            else:
                hint = UNIT_HINTS.get(str(unit).strip().lower())
                if hint:
                    issues.append(Issue("info", fw, f"{name} 单位 {unit!r}：{hint}"))

            date_text = str(field.get("date") or "").strip()
            if not date_text:
                issues.append(
                    Issue("warn", fw, f"{name or '?'} 缺少 date（会导致 K1 判错）")
                )
            elif not DATE_RE.match(date_text):
                issues.append(
                    Issue("error", fw, f"{name} 日期格式应为 YYYY-MM-DD：{date_text!r}")
                )
            else:
                try:
                    date.fromisoformat(date_text)
                except ValueError:
                    issues.append(
                        Issue("error", fw, f"{name} 日期不合法：{date_text!r}")
                    )

            if "abnormal" not in field:
                issues.append(
                    Issue("warn", fw, f"{name or '?'} 缺少 abnormal（默认按 false 处理）")
                )
            elif not isinstance(field["abnormal"], bool):
                issues.append(
                    Issue(
                        "error",
                        fw,
                        f"{name} abnormal 必须是 true/false，当前 {field['abnormal']!r}",
                    )
                )

        for name, count in seen_names.items():
            if count > 1:
                issues.append(
                    Issue(
                        "error",
                        where,
                        f"同一文件内字段名重复 {count} 次：{name}（会导致统计覆盖）",
                    )
                )

    # ---- 3. 孤儿文件（有样本但没标注）----
    if files_dir.exists():
        disk_files = {
            f"files/{f.name}" for f in files_dir.iterdir() if f.is_file() and f.name != ".gitkeep"
        }
        orphans = sorted(disk_files - annotated_files)
        for orphan in orphans:
            issues.append(Issue("warn", "files/", f"有样本但无标注：{orphan}"))

    # ---- 4. 重复样本（同 stem 不同扩展名）----
    stems: dict[str, list[str]] = defaultdict(list)
    for rel in annotated_files:
        stems[Path(rel).stem].append(rel)
    for stem, rels in stems.items():
        if len(rels) > 1:
            issues.append(
                Issue(
                    "warn",
                    "files/",
                    f"疑似重复样本（同名前缀）：{', '.join(sorted(rels))}",
                )
            )

    # ---- 5. 统计摘要 ----
    total_fields = sum(len(r.get("fields") or []) for r in rows)
    abnormal_fields = sum(
        1
        for r in rows
        for f in (r.get("fields") or [])
        if isinstance(f, dict) and f.get("abnormal") is True
    )
    subjects = Counter(str(r.get("subject") or "?") for r in rows)
    summary = {
        "annotated_files": len(rows),
        "disk_files": len(
            [f for f in files_dir.iterdir() if f.is_file() and f.name != ".gitkeep"]
        )
        if files_dir.exists()
        else 0,
        "total_fields": total_fields,
        "abnormal_fields": abnormal_fields,
        "subjects": dict(subjects),
        "errors": sum(1 for i in issues if i.level == "error"),
        "warnings": sum(1 for i in issues if i.level == "warn"),
    }
    return issues, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="dataset/kpi_k1k2", help="数据集目录")
    ap.add_argument("--strict", action="store_true", help="有 warning 也返回非零")
    args = ap.parse_args()

    dir_path = Path(args.dir)
    issues, summary = check(dir_path)

    for issue in issues:
        print(issue)

    print("\n" + "=" * 60)
    print("标注体检结果")
    print("=" * 60)
    for key, value in summary.items():
        print(f"  {key}: {value}")

    errors = summary.get("errors", 0)
    warnings = summary.get("warnings", 0)
    if errors:
        print(f"\n❌ 发现 {errors} 个错误，必须先修复才能跑分。")
        return 1
    if warnings and args.strict:
        print(f"\n⚠️  发现 {warnings} 个警告（--strict 视为失败）。")
        return 1
    if warnings:
        print(f"\n⚠️  {warnings} 个警告（不阻塞，但建议处理）。")
    print("\n✅ 结构校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
