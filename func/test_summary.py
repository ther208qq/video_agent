"""summary 的测试：数值进得对、显著口径统一、没有因果和趋势措辞。

用法：python func/test_summary.py
（也可以 pytest func/test_summary.py）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import CorrelationAnalysis, GroupComparison, GroupSummary
from tools.summary import summarize_correlation, summarize_group_comparison

#: 真实 demo 数值，直接拿来当基准
TRUE_MEAN = 0.16416253543307088
FALSE_MEAN = 0.15146330057803467
DIFFERENCE = 0.012699234855036212
P_VALUE = 0.06830134498719104
R_COEF = 0.104233
R_P_VALUE = 0.071428

#: 非显著时不允许出现的措辞（“统计显著”单独判，见 test_no_significance_claim）
HEDGING = ("趋势", "边缘显著", "倾向于", "影响", "导致", "造成", "因为", "使得", "提升", "降低")


def make_group_comparison(p_value, target="like_rate"):
    return GroupComparison(
        feature="has_conflict",
        target=target,
        true_group=GroupSummary(n=127, mean=TRUE_MEAN, median=0.159423),
        false_group=GroupSummary(n=173, mean=FALSE_MEAN, median=0.148437),
        mean_difference=DIFFERENCE,
        t_statistic=1.8305698891745044,
        p_value=p_value,
    )


def make_correlation(correlation=R_COEF, p_value=R_P_VALUE, n=300):
    return CorrelationAnalysis(
        feature="emotion_score", target="like_rate",
        correlation=correlation, p_value=p_value, n=n,
    )


def text_of(summary):
    return summary.key_result + summary.interpretation


def test_group_comparison_significant():
    summary = summarize_group_comparison(make_group_comparison(0.02))

    assert summary.analysis_type == "group_comparison"
    assert "达到统计显著" in summary.interpretation
    assert "未达到" not in summary.interpretation


def test_group_comparison_not_significant():
    summary = summarize_group_comparison(make_group_comparison(P_VALUE))

    assert summary.analysis_type == "group_comparison"
    assert "未达到统计显著" in summary.interpretation


def test_group_means_and_difference_in_text():
    summary = summarize_group_comparison(make_group_comparison(P_VALUE))

    assert "has_conflict=true" in summary.key_result
    assert "16.42%" in summary.key_result
    assert "15.15%" in summary.key_result
    assert "1.27 个百分点" in summary.key_result
    assert summary.feature == "has_conflict"
    assert summary.target == "like_rate"


def test_group_p_value_in_text_and_artifact():
    summary = summarize_group_comparison(make_group_comparison(P_VALUE))

    assert "0.0683" in summary.interpretation
    assert "Welch's t-test" in summary.interpretation
    assert summary.statistical_result["p_value"] == P_VALUE


def test_group_sample_size():
    summary = summarize_group_comparison(make_group_comparison(P_VALUE))

    assert summary.sample_size == 300, "127 + 173"
    assert summary.statistical_result["true_group"]["n"] == 127
    assert summary.statistical_result["false_group"]["n"] == 173


def test_group_input_not_mutated():
    comparison = make_group_comparison(P_VALUE)
    before = comparison.model_dump()

    summarize_group_comparison(comparison)

    assert comparison.model_dump() == before, "不该改动入参 artifact"


def test_group_raw_values_untouched():
    """摘要里的百分比只是展示，artifact 里的原始浮点必须一位不变。"""
    comparison = make_group_comparison(P_VALUE)
    summary = summarize_group_comparison(comparison)

    assert summary.statistical_result["true_group"]["mean"] == TRUE_MEAN
    assert summary.statistical_result["false_group"]["mean"] == FALSE_MEAN
    assert summary.statistical_result["mean_difference"] == DIFFERENCE
    assert summary.statistical_result["true_group"]["median"] == 0.159423


def test_positive_correlation():
    summary = summarize_correlation(make_correlation())

    assert summary.analysis_type == "correlation"
    assert "0.1042" in summary.key_result
    assert "0.0714" in summary.key_result
    assert summary.feature == "emotion_score"
    assert summary.target == "like_rate"


def test_negative_correlation():
    summary = summarize_correlation(make_correlation(correlation=-0.55, p_value=0.0001))

    assert "-0.5500" in summary.key_result
    assert "达到统计显著" in summary.interpretation
    assert "未达到" not in summary.interpretation


def test_correlation_not_significant():
    summary = summarize_correlation(make_correlation())

    assert "未达到统计显著" in summary.interpretation


def test_correlation_sample_size():
    summary = summarize_correlation(make_correlation(n=300))

    assert summary.sample_size == 300
    assert "n=300" in summary.key_result
    assert summary.statistical_result["n"] == 300


def test_correlation_input_not_mutated():
    correlation = make_correlation()
    before = correlation.model_dump()

    summarize_correlation(correlation)

    assert correlation.model_dump() == before


def test_no_significance_claim_when_not_significant():
    """p >= 0.05 时，每一处「统计显著」都必须是「未达到统计显著」的一部分。

    注意不能直接断言 '"统计显著" not in text'——「未达到统计显著」本身含这个词，
    那样写会把正确的措辞也判成错误。
    """
    for p_value in (0.05, P_VALUE, 0.5, 0.999):
        summaries = (
            summarize_group_comparison(make_group_comparison(p_value)),
            summarize_correlation(make_correlation(p_value=p_value)),
        )
        for summary in summaries:
            text = text_of(summary)
            assert text.count("统计显著") == text.count("未达到统计显著"), text


def test_significance_claim_allowed_when_significant():
    for p_value in (0.0499, 0.01, 0.0001):
        summaries = (
            summarize_group_comparison(make_group_comparison(p_value)),
            summarize_correlation(make_correlation(p_value=p_value)),
        )
        for summary in summaries:
            text = text_of(summary)
            assert "达到统计显著" in text
            assert "未达到" not in text


def test_no_hedging_or_causal_wording():
    summaries = (
        summarize_group_comparison(make_group_comparison(P_VALUE)),
        summarize_group_comparison(make_group_comparison(0.02)),
        summarize_correlation(make_correlation()),
        summarize_correlation(make_correlation(correlation=-0.55, p_value=0.0001)),
    )
    for summary in summaries:
        text = text_of(summary)
        for word in HEDGING:
            assert word not in text, f"出现了 {word!r}: {text}"


def main():
    tests = [
        test_group_comparison_significant,
        test_group_comparison_not_significant,
        test_group_means_and_difference_in_text,
        test_group_p_value_in_text_and_artifact,
        test_group_sample_size,
        test_group_input_not_mutated,
        test_group_raw_values_untouched,
        test_positive_correlation,
        test_negative_correlation,
        test_correlation_not_significant,
        test_correlation_sample_size,
        test_correlation_input_not_mutated,
        test_no_significance_claim_when_not_significant,
        test_significance_claim_allowed_when_significant,
        test_no_hedging_or_causal_wording,
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
