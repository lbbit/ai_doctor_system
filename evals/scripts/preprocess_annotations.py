#!/usr/bin/env python3
"""标注预处理（K1/K2）：修正可确定的低级错误 + 分离非化验项。

设计原则（AGENTS.md「AI 只在判断环节」）：
- **只做确定性修正**：能由规则唯一确定的才改（如单位缺失回填、日期规范化）。
- **不猜内容**：无法确定的（如影像报告是否计入 KPI）不臆造，改为
  标记并输出报告，由人工决策。
- **幂等**：可重复运行，结果一致。
- **保留原文件**：默认写新文件，`--in-place` 才覆盖（并留 .bak）。

修正项：
1. 日期规范化：`2025/1/6`、`2025.1.6`、`2025年1月6日` → `YYYY-MM-DD`
2. 数值规范化：全角数字/全角小数点/前后空白/中文括号 → 半角
3. 明确可推断的单位回填（白名单，见 UNIT_BACKFILL）
4. `abnormal` 缺失 → 按 false 补全（并在报告中列出）
5. 字段名去首尾空白、中文括号统一
6. 分类为 primary（化验）与 excluded（影像/定性/血型等非 KPI 项）
7. 同一文件内重复字段名 → 报告 error（不自动改名，避免误合并成对指标）

输出：
- `<out>.jsonl`：清洗后的标注
- `<out>_report.md`：逐文件分类与待人工确认项

用法：
    python scripts/preprocess_annotations.py --dir dataset/kpi_k1k2
    python scripts/preprocess_annotations.py --dir dataset/kpi_k1k2 --in-place
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 可推断单位白名单
# 仅收录"临床上唯一确定"的项，避免猜错造成假通过。
UNIT_BACKFILL: dict[str, str] = {
    "血小板比容": "%",
    "红细胞比容": "%",
    "红细胞压积": "%",
    "白球比": "",
    "国际标准化比值": "",
    "PH值": "",
    "pH值": "",
}

# 非化验项关键词（影像/内镜/病理/形态学描述）→ 排除出 K1/K2 分母
IMAGING_KEYWORDS = (
    "超声诊断", "影像诊断", "CT诊断", "MR诊断", "核磁诊断",
    "超声所见", "影像所见", "检查结论", "诊断意见",
    "腹腔积液范围", "低回声区", "最大径线",
)
# 定性检验项（核酸/抗原/抗体/培养/血型/镜检形态）→ 单独归类
QUALITATIVE_KEYWORDS = (
    "核酸", "抗原", "抗体IgM", "抗体IgG", "培养", "药敏", "涂片",
    "血型", "Rh", "ABO",
    "颜色", "性状", "外观", "镜检", "原虫", "虫卵", "霉菌孢子",
    "脂肪球", "上皮细胞", "粘液丝", "真菌", "结晶", "管型",
    "隐血", "亚硝酸盐", "胆红素", "尿胆原", "酮体", "白细胞酯酶",
    "维生素C", "蛋白", "糖", "比重", "酸碱度",
)

DATE_PATTERNS = (
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$"), "ymd"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
)
FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９．－＜＞＋（）｡，",
    "0123456789.-<>+(),,",
)


def normalize_text(value: Any) -> str:
    """全角转半角 + 去首尾空白（不做语义改写）。"""
    if value is None:
        return ""
    return str(value).translate(FULLWIDTH_MAP).strip()


def normalize_date(raw: Any) -> tuple[str, bool]:
    """返回 (规范化日期, 是否被修改)。无法解析时原样返回。"""
    text = normalize_text(raw)
    if not text:
        return "", False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            date.fromisoformat(text)
            return text, False
        except ValueError:
            return text, False
    for pattern, _kind in DATE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d).isoformat(), True
        except ValueError:
            return text, False
    return text, False


def classify(name: str) -> str:
    """primary=计入 KPI 的化验项；excluded=影像；qualitative=定性检验。"""
    if any(k in name for k in IMAGING_KEYWORDS):
        return "excluded"
    if any(k in name for k in QUALITATIVE_KEYWORDS):
        return "qualitative"
    return "primary"


def preprocess_row(row: dict) -> tuple[dict, dict]:
    """清洗单行标注，返回 (新行, 统计信息)。"""
    stats: dict[str, Any] = {
        "date_fixed": [],
        "unit_backfilled": [],
        "abnormal_filled": [],
        "value_normalized": [],
        "dup_names": [],
        "classes": Counter(),
    }

    out = {
        "file": row.get("file", ""),
        "subject": row.get("subject", ""),
        "fields": [],
        "notes": row.get("notes", ""),
    }
    if "documentId" in row:
        out["documentId"] = row["documentId"]

    name_counter: Counter[str] = Counter()
    for field in row.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = normalize_text(field.get("name"))
        if not name:
            continue

        raw_value = field.get("value")
        value = normalize_text(raw_value)
        if value != str(raw_value if raw_value is not None else "").strip():
            stats["value_normalized"].append(f"{name}: {raw_value!r} → {value!r}")

        unit = normalize_text(field.get("unit"))
        if not unit and name in UNIT_BACKFILL:
            unit = UNIT_BACKFILL[name]
            if unit:
                stats["unit_backfilled"].append(f"{name} → {unit!r}")

        new_date, date_changed = normalize_date(field.get("date"))
        if date_changed:
            stats["date_fixed"].append(f"{name}: {field.get('date')!r} → {new_date!r}")

        abnormal = field.get("abnormal")
        if not isinstance(abnormal, bool):
            abnormal = False
            stats["abnormal_filled"].append(name)

        cls = classify(name)
        stats["classes"][cls] += 1
        name_counter[name] += 1

        out["fields"].append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "date": new_date,
                "abnormal": abnormal,
                "kpiClass": cls,
            }
        )

    for name, count in name_counter.items():
        if count > 1:
            stats["dup_names"].append(f"{name} ×{count}")

    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="dataset/kpi_k1k2")
    ap.add_argument("--in-place", action="store_true", help="覆盖原文件（先备份 .bak）")
    ap.add_argument("--keep-kpi-class", action="store_true",
                    help="保留 kpiClass 字段（默认保留，跑分脚本会忽略）")
    args = ap.parse_args()

    root = Path(args.dir)
    src = root / "annotations.jsonl"
    if not src.exists():
        print(f"找不到 {src}", file=sys.stderr)
        return 2

    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]

    cleaned: list[dict] = []
    report_lines: list[str] = []
    totals = Counter()
    dup_errors: list[str] = []

    for row in rows:
        new_row, stats = preprocess_row(row)
        cleaned.append(new_row)
        totals.update(stats["classes"])
        totals["date_fixed"] += len(stats["date_fixed"])
        totals["unit_backfilled"] += len(stats["unit_backfilled"])
        totals["abnormal_filled"] += len(stats["abnormal_filled"])
        totals["value_normalized"] += len(stats["value_normalized"])

        fname = Path(new_row["file"]).name
        if stats["dup_names"]:
            dup_errors.append(f"{fname}: {stats['dup_names']}")

        if any(stats[k] for k in ("date_fixed", "unit_backfilled", "abnormal_filled", "dup_names")):
            report_lines.append(f"\n### {fname}")
            for key, label in (
                ("date_fixed", "日期修正"),
                ("unit_backfilled", "单位回填"),
                ("abnormal_filled", "abnormal 补全"),
                ("value_normalized", "数值规范化"),
                ("dup_names", "重复字段名"),
            ):
                if stats[key]:
                    report_lines.append(f"- {label}: {stats[key]}")

    # ---- 输出 ----
    if args.in_place:
        backup = src.with_suffix(".jsonl.bak")
        backup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        out_path = src
        print(f"已备份原文件 → {backup}")
    else:
        out_path = root / "annotations.clean.jsonl"

    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in cleaned:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_path = root / "annotations_report.md"
    with report_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 标注预处理报告\n\n")
        fh.write("## 汇总\n\n")
        fh.write(f"- 文件数：{len(cleaned)}\n")
        fh.write(f"- 字段总数：{sum(len(r['fields']) for r in cleaned)}\n")
        fh.write(f"- 计入 KPI（primary）：{totals['primary']}\n")
        fh.write(f"- 定性检验（qualitative，建议排除）：{totals['qualitative']}\n")
        fh.write(f"- 影像/描述（excluded，建议排除）：{totals['excluded']}\n")
        fh.write(f"- 日期修正：{totals['date_fixed']}\n")
        fh.write(f"- 单位回填：{totals['unit_backfilled']}\n")
        fh.write(f"- abnormal 补全：{totals['abnormal_filled']}\n")
        fh.write(f"- 数值规范化：{totals['value_normalized']}\n")
        if dup_errors:
            fh.write("\n## ⚠️ 需人工处理：重复字段名\n\n")
            for item in dup_errors:
                fh.write(f"- {item}\n")
        fh.write("\n## 逐文件明细\n")
        fh.write("\n".join(report_lines) if report_lines else "\n（无需修正项）\n")
        fh.write("\n\n## 分类说明\n\n")
        fh.write("- `primary`：常规数值型化验项，计入 K1/K2\n")
        fh.write("- `qualitative`：核酸/抗原/培养/尿常规定性等，单位为空白，判分口径需单独约定\n")
        fh.write("- `excluded`：影像/内镜/形态学描述，K1/K2 不应统计\n")
        fh.write("\n> 说明：本脚本只做确定性修正；分类为建议，是否排除由人工确认。\n")

    print(f"清洗后写入 → {out_path}")
    print(f"报告写入 → {report_path}")
    print("\n汇总：")
    for key, label in (
        ("primary", "计入 KPI"),
        ("qualitative", "定性检验"),
        ("excluded", "影像/描述"),
        ("date_fixed", "日期修正"),
        ("unit_backfilled", "单位回填"),
        ("abnormal_filled", "abnormal 补全"),
        ("value_normalized", "数值规范化"),
    ):
        print(f"  {label}: {totals[key]}")
    if dup_errors:
        print(f"\n⚠️ {len(dup_errors)} 个文件存在重复字段名，见报告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
