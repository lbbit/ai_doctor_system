"""清理历史脏指标（AI 早期把排版碎片建成标准指标的遗留）。

用法（容器内）：
    python /app/scripts/clean_indicator_dictionary.py           # 仅检查
    python /app/scripts/clean_indicator_dictionary.py --apply   # 实际停用

策略：
- 默认 **停用**（active=False）而不是删除，保留审计可追溯性；
- 已被 Observation 引用的不删除、只停用，避免级联影响历史记录；
- 输出将停用的清单，便于人工复核后再决定是否物理清理。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from sqlalchemy import func, select  # noqa: E402

from app.db.models import IndicatorDictionary, Observation  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.preprocessing.indicator_aligner import is_plausible_indicator_name  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        rows = db.scalars(select(IndicatorDictionary)).all()
        noisy = []
        for row in rows:
            ok, reason = is_plausible_indicator_name(row.name_cn or "")
            if ok:
                continue
            used = db.scalar(
                select(func.count()).select_from(Observation).where(
                    Observation.dictionary_id == row.id
                )
            )
            noisy.append((row, reason, int(used or 0)))

        print(f"checked={len(rows)} noisy={len(noisy)} apply={apply}")
        for row, reason, used in noisy:
            print(f"  {row.code:24s} {row.name_cn!r:24s} reason={reason} used={used}")
            if apply:
                row.active = False
        if apply and noisy:
            db.commit()
            print(f"deactivated={len(noisy)}")
        elif noisy:
            print("dry-run：加 --apply 才会停用")
    finally:
        db.close()


if __name__ == "__main__":
    main()
