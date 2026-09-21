"""读固定数据集，跑一次分组比较和一次相关，各自生成摘要。不调 LLM。

用法：python func/summary_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import compare_groups, correlate
from tools.summary import summarize_correlation, summarize_group_comparison

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "analysis_dataset.json"


def show(artifact, summary):
    print("--- 原始 artifact ---")
    print(json.dumps(artifact.model_dump(), ensure_ascii=False, indent=2))
    print("--- AnalysisSummary ---")
    print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))
    print("--- 自然语言摘要 ---")
    print(summary.key_result + summary.interpretation)
    print()


def main():
    records = json.loads(DATASET.read_text(encoding="utf-8"))["records"]
    print(f"{DATASET.relative_to(ROOT).as_posix()}: {len(records)} 条\n")

    comparison = compare_groups(records, "has_conflict", "like_rate")
    show(comparison, summarize_group_comparison(comparison))

    correlation = correlate(records, "emotion_score", "like_rate")
    show(correlation, summarize_correlation(correlation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
