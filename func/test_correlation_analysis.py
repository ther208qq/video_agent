"""correlation_analysis 的测试：手算基准 + 各条校验分支。

用法：python func/test_correlation_analysis.py
（也可以 pytest func/test_correlation_analysis.py）
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.statistics import MIN_PAIRS, correlate


def close(actual, expected, tol=1e-9):
    assert math.isclose(actual, expected, rel_tol=tol, abs_tol=tol), f"{actual!r} != {expected!r}"


def raises(fn, *fragments):
    """断言抛 ValueError，且错误信息里点名了原因。"""
    try:
        fn()
    except ValueError as e:
        for fragment in fragments:
            assert fragment in str(e), f"错误信息里没有 {fragment!r}: {e}"
        return
    raise AssertionError("本该抛 ValueError，却正常返回了")


def test_known_values():
    """手算基准：x=[-2,-1,0,1,2]，y=[-1,-2,0,1,2]。

    两列均值都是 0，sum(x²)=sum(y²)=10，sum(xy)=9，因此 r = 9 / sqrt(10×10) = 0.9。
    p 不查 pearsonr，由 t 变换独立推出：t = r·sqrt((n-2)/(1-r²)) = 3.576237364，
    df = n-2 = 3，p = 2·sf(t, 3) = 0.037386073468。
    """
    records = [{"x": a, "y": b} for a, b in zip([-2, -1, 0, 1, 2], [-1, -2, 0, 1, 2])]
    result = correlate(records, "x", "y")

    assert result.n == 5
    close(result.correlation, 0.9)
    close(result.p_value, 0.037386073468)
    assert result.feature == "x"
    assert result.target == "y"
    assert result.method == "Pearson correlation"


def test_perfect_positive_correlation():
    records = [{"x": a, "y": b} for a, b in zip([1, 2, 3, 4], [2, 4, 6, 8])]
    result = correlate(records, "x", "y")

    assert result.n == 4
    close(result.correlation, 1.0)
    close(result.p_value, 0.0)


def test_non_numeric_rejected():
    text_feature = [{"x": a, "y": b} for a, b in zip([1, 2, "a", 4], [2, 4, 6, 8])]
    raises(lambda: correlate(text_feature, "x", "y"), "不是数值变量")

    text_target = [{"x": a, "y": b} for a, b in zip([1, 2, 3, 4], [2, 4, "6", 8])]
    raises(lambda: correlate(text_target, "x", "y"), "不是数值变量")

    # 数字字符串也拒绝，不做隐式转换
    digits_as_text = [{"x": str(v), "y": b} for v, b in zip([1, 2, 3, 4], [2, 4, 6, 8])]
    raises(lambda: correlate(digits_as_text, "x", "y"), "不是数值变量")


def test_missing_values_pairwise_deletion():
    """只保留完整 pair：剩 (1,2) (2,4) (4,8) 三点，恰好在 y=2x 上，所以 r 必须正好等于 1。

    若把缺失值当成 0，会多出 (0,6) 这个点，r 不可能等于 1 —— 这一条同时锁住了「不把缺失当 0」。
    """
    missing_feature = [{"x": a, "y": b} for a, b in zip([1, 2, None, 4], [2, 4, 6, 8])]
    result = correlate(missing_feature, "x", "y")
    assert result.n == 3
    close(result.correlation, 1.0)

    missing_target = [{"x": a, "y": b} for a, b in zip([1, 2, 3, 4], [2, None, 6, 8])]
    result = correlate(missing_target, "x", "y")
    assert result.n == 3
    close(result.correlation, 1.0)


def test_insufficient_pairs_rejected():
    below = [{"x": a, "y": b} for a, b in zip([1, 2, None, None], [2, 4, 6, 8])]
    raises(lambda: correlate(below, "x", "y"), "只剩 2 对", f"至少需要 {MIN_PAIRS} 对")


def test_constant_variable_rejected():
    constant_feature = [{"x": 1, "y": b} for b in [2, 4, 6, 8]]
    raises(lambda: correlate(constant_feature, "x", "y"), "是常量", "没有方差")

    constant_target = [{"x": a, "y": 3} for a in [1, 2, 3, 4]]
    raises(lambda: correlate(constant_target, "x", "y"), "是常量", "没有方差")


def test_missing_field_rejected():
    records = [{"x": a, "y": b} for a, b in zip([1, 2, 3, 4], [2, 4, 6, 8])]
    raises(lambda: correlate(records, "nope", "y"), "没有字段")
    raises(lambda: correlate(records, "x", "nope"), "没有字段")


def main():
    tests = [
        test_known_values,
        test_perfect_positive_correlation,
        test_non_numeric_rejected,
        test_missing_values_pairwise_deletion,
        test_insufficient_pairs_rejected,
        test_constant_variable_rejected,
        test_missing_field_rejected,
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
