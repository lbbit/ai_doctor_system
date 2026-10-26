#!/usr/bin/env python
"""冷启动指标字典播种（幂等，可重复执行）。

用法：
  cd server && python ../scripts/seed_dictionary.py
  容器内：python /app/scripts/seed_dictionary.py（容器启动时也会自动执行）

数据源在 `server/app/data/indicator_dictionary.py`（随 app 打包，保证容器内可用）。
医学定稿由医学顾问评审后修改该文件并重跑本脚本（按 code upsert，不会重复插入）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from app.data.dictionary_seed import ensure_seeded  # noqa: E402


def main() -> None:
    stats = ensure_seeded()
    print(
        "seed_dictionary done: total={total} created={created} updated={updated} "
        "top_trend={topTrend} alreadyPresent={alreadyPresent}".format(**stats)
    )


if __name__ == "__main__":
    main()
