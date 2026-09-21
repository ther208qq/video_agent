"""读固定数据集，跑一次分组比较，再把它转成柱状图描述打印出来。不调 LLM。

用法：python func/visualization_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import compare_groups
from tools.visualization import visualize_group_comparison

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "analysis_dataset.json"
PAIRS = (("has_conflict", "like_rate"), ("has_hook", "like_rate"))


def main():
    records = json.loads(DATASET.read_text(encoding="utf-8"))["records"]
    print(f"{DATASET.relative_to(ROOT).as_posix()}: {len(records)} 条\n")

    for feature, target in PAIRS:
        comparison = compare_groups(records, feature, target)
        spec = visualize_group_comparison(comparison)
        print(f"--- {feature} -> {target} ---")
        print(json.dumps(spec.model_dump(), ensure_ascii=False, indent=2))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
