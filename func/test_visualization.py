"""visualize_group_comparison 的测试：搬运正确、且确实没有重新计算。

用法：python func/test_visualization.py
（也可以 pytest func/test_visualization.py）
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import GroupComparison, GroupSummary
from tools.visualization import VisualizationSpec, visualize_group_comparison

#: 故意用一组「不像真实统计结果」的数：均值一正一负、p 值 0.999。
#: mean_difference 特意取 7.5，让它既不等于 true.mean - false.mean（124.69），
#: 也不等于两组均值的任何简单组合——重算过的实现不可能吐出 7.5。
TRUE_GROUP = GroupSummary(n=127, mean=123.456, median=120.0)
FALSE_GROUP = GroupSummary(n=173, mean=-1.234, median=-2.0)


def make_comparison(**overrides):
    payload = {
        "feature": "has_conflict",
        "target": "like_rate",
        "true_group": TRUE_GROUP,
        "false_group": FALSE_GROUP,
        "mean_difference": 7.5,
        "t_statistic": 1.2345,
        "p_value": 0.999,
        "method": "Welch's t-test",
    }
    payload.update(overrides)
    return GroupComparison(**payload)


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12), f"{actual!r} != {expected!r}"


def test_bar_spec_shape():
    spec = visualize_group_comparison(make_comparison())

    assert isinstance(spec, VisualizationSpec)
    assert spec.chart_type == "bar"
    assert len(spec.series) == 2, "第一版只有 true / false 两根柱子"
    assert spec.title, "标题不能为空"


def test_group_means_map_to_bars():
    spec = visualize_group_comparison(make_comparison())

    true_bar, false_bar = spec.series
    assert true_bar.name == "has_conflict = true"
    assert false_bar.name == "has_conflict = false"
    close(true_bar.value, TRUE_GROUP.mean)
    close(false_bar.value, FALSE_GROUP.mean)
    assert true_bar.n == TRUE_GROUP.n
    assert false_bar.n == FALSE_GROUP.n


def test_feature_and_target_preserved():
    spec = visualize_group_comparison(make_comparison(feature="has_hook", target="share_rate"))

    assert spec.x_label == "has_hook"
    assert spec.y_label == "share_rate"
    assert spec.series[0].name == "has_hook = true"
    assert spec.series[1].name == "has_hook = false"
    assert "has_hook" in spec.title and "share_rate" in spec.title


def test_statistics_preserved_as_metadata():
    comparison = make_comparison()
    spec = visualize_group_comparison(comparison)

    close(spec.metadata["p_value"], comparison.p_value)
    close(spec.metadata["mean_difference"], comparison.mean_difference)
    close(spec.metadata["t_statistic"], comparison.t_statistic)
    assert spec.metadata["method"] == comparison.method
    # 中位数只在原始结果里，必须靠 metadata 留住
    close(spec.metadata["true_group"]["median"], TRUE_GROUP.median)
    close(spec.metadata["false_group"]["median"], FALSE_GROUP.median)
    assert spec.metadata["true_group"]["n"] == TRUE_GROUP.n
    assert spec.metadata["false_group"]["n"] == FALSE_GROUP.n


def test_nothing_is_recomputed():
    """入参的怪数字必须原样透传——重算过一次就露馅。"""
    spec = visualize_group_comparison(make_comparison())

    close(spec.series[0].value, 123.456)
    close(spec.series[1].value, -1.234)
    close(spec.metadata["p_value"], 0.999)
    # 入参说均值差是 7.5，而算出来的 true.mean - false.mean 是 124.69。
    # 断言 7.5 原样透传，重算过的实现会在这里失败。
    close(spec.metadata["mean_difference"], 7.5)


def test_input_is_not_mutated_and_spec_is_serializable():
    comparison = make_comparison()
    before = comparison.model_dump()

    spec = visualize_group_comparison(comparison)

    assert comparison.model_dump() == before, "不该改动入参"
    dumped = json.loads(json.dumps(spec.model_dump(), ensure_ascii=False))
    assert dumped["series"][0]["name"] == "has_conflict = true"
    assert set(dumped) == {"chart_type", "title", "x_label", "y_label", "series", "metadata"}


def main():
    tests = [
        test_bar_spec_shape,
        test_group_means_map_to_bars,
        test_feature_and_target_preserved,
        test_statistics_preserved_as_metadata,
        test_nothing_is_recomputed,
        test_input_is_not_mutated_and_spec_is_serializable,
    ]
    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}: {e}")
        else:
            print(f"PASS {test.__name__}")

    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
