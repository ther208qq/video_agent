"""在固定的 300 条数据集上跑 Pearson 相关。只读 analysis_dataset.json，不调 LLM。

用法：python func/correlation_analysis_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import correlate

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "analysis_dataset.json"
PAIRS = (("emotion_score", "like_rate"), ("question_count", "like_rate"))


def main():
    records = json.loads(DATASET.read_text(encoding="utf-8"))["records"]
    print(f"{DATASET.relative_to(ROOT).as_posix()}: {len(records)} 条\n")

    for feature, target in PAIRS:
        result = correlate(records, feature, target)
        print(f"feature={result.feature} target={result.target} n={result.n} "
              f"correlation={result.correlation:.6f} p_value={result.p_value:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
