"""比较二元特征分组的互动表现差异、两个数值变量的相关。统计量全部在 pandas/scipy 里算，LLM 不参与。"""

import math

import pandas as pd
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from scipy import stats

#: 每组至少要有这么多条才能做 t 检验
MIN_GROUP_SIZE = 2

#: 至少要有这么多对完整数据才能算相关。n < 3 时 r 恒为 ±1，不是测量结果而是算术必然
MIN_PAIRS = 3


class GroupSummary(BaseModel):
    """一组样本的分布摘要。"""

    n: int = Field(description="样本数")
    mean: float = Field(description="均值")
    median: float = Field(description="中位数")


class GroupComparison(BaseModel):
    """二元特征分组的对比结果。"""

    feature: str = Field(description="用作分组的二元特征字段名")
    target: str = Field(description="被比较的数值字段名")
    true_group: GroupSummary = Field(description="feature 为 true 的一组")
    false_group: GroupSummary = Field(description="feature 为 false 的一组")
    mean_difference: float = Field(description="true 组均值减 false 组均值")
    method: str = Field(default="Welch's t-test", description="使用的检验方法")
    t_statistic: float = Field(description="t 统计量")
    p_value: float = Field(description="p 值")


def compare_groups(dataset, feature, target) -> GroupComparison:
    """按二元特征分组比较 target 的均值差异，做 Welch t 检验。"""
    frame = pd.DataFrame(dataset)
    for name in (feature, target):
        if name not in frame.columns:
            raise ValueError(
                f"数据集里没有字段 {name!r}，现有字段: {list(frame.columns)}"
            )
    if not pd.api.types.is_numeric_dtype(frame[target]):
        raise ValueError(
            f"target {target!r} 不是数值变量，dtype={frame[target].dtype}"
        )

    column = frame[feature].dropna()
    if column.dtype == bool:
        pass
    elif set(column.unique()) <= {0, 1} and len(column):
        pass
    else:
        raise ValueError(
            f"feature {feature!r} 不是二元变量，取值: {sorted(set(column))[:5]}"
        )

    # 两列任一为空的行都丢掉，避免把缺失值当成 false
    clean = pd.DataFrame({"flag": frame[feature], "value": pd.to_numeric(frame[target], errors="coerce")})
    clean = clean.dropna()
    clean["flag"] = clean["flag"].astype(bool)

    groups = {
        "true": clean.loc[clean["flag"], "value"],
        "false": clean.loc[~clean["flag"], "value"],
    }
    for label, values in groups.items():
        if len(values) < MIN_GROUP_SIZE:
            raise ValueError(
                f"feature={label} 组样本不足：{len(values)} 条，至少需要 {MIN_GROUP_SIZE} 条"
            )

    t_statistic, p_value = stats.ttest_ind(
        groups["true"], groups["false"], equal_var=False
    )

    def summary(values):
        return GroupSummary(n=len(values), mean=float(values.mean()), median=float(values.median()))

    return GroupComparison(
        feature=feature,
        target=target,
        true_group=summary(groups["true"]),
        false_group=summary(groups["false"]),
        mean_difference=float(groups["true"].mean() - groups["false"].mean()),
        t_statistic=float(t_statistic),
        p_value=float(p_value),
    )


def _describe(result: GroupComparison) -> str:
    return (
        f"{result.feature} vs {result.target}: "
        f"true n={result.true_group.n} mean={result.true_group.mean:.6f} "
        f"median={result.true_group.median:.6f}; "
        f"false n={result.false_group.n} mean={result.false_group.mean:.6f} "
        f"median={result.false_group.median:.6f}; "
        f"mean_difference={result.mean_difference:.6f} "
        f"t={result.t_statistic:.6f} p={result.p_value:.6f}"
    )


@tool("group_comparison", parse_docstring=True, response_format="content_and_artifact")
def group_comparison(dataset: list[dict], feature: str, target: str):
    """比较一个二元内容特征在两组之间的数值表现差异。

    按 feature 把样本分成 true / false 两组，比较各自的 target 均值和中位数，
    并做 Welch t 检验。只返回数字，不下结论。

    Args:
        dataset: 记录列表，每条至少包含 feature 和 target 两个字段
        feature: 用作分组的二元特征字段名，例如 "has_hook"
        target: 被比较的数值字段名，例如 "like_rate"

    Returns:
        两组的样本数/均值/中位数、均值差、t 统计量和 p 值，结构化结果在 artifact 里
    """
    result = compare_groups(dataset, feature, target)
    return _describe(result), result


class CorrelationAnalysis(BaseModel):
    """两个数值变量之间的 Pearson 相关结果。"""

    feature: str = Field(description="参与相关的数值字段名")
    target: str = Field(description="参与相关的另一个数值字段名")
    n: int = Field(description="实际参与计算的完整数据对数（已剔除缺失）")
    correlation: float = Field(description="Pearson 相关系数")
    p_value: float = Field(description="p 值")
    method: str = Field(default="Pearson correlation", description="使用的检验方法")


def correlate(dataset, feature, target) -> CorrelationAnalysis:
    """对两个数值变量做 Pearson 相关，逐对删除缺失值。"""
    frame = pd.DataFrame(dataset)
    for name in (feature, target):
        if name not in frame.columns:
            raise ValueError(
                f"数据集里没有字段 {name!r}，现有字段: {list(frame.columns)}"
            )
        if not pd.api.types.is_numeric_dtype(frame[name]):
            raise ValueError(
                f"{name!r} 不是数值变量，dtype={frame[name].dtype}，无法做相关"
            )

    # 逐对删除：两列任一为空的行都丢掉，避免把缺失值当成 0
    pairs = pd.DataFrame({
        feature: pd.to_numeric(frame[feature], errors="coerce"),
        target: pd.to_numeric(frame[target], errors="coerce"),
    }).dropna()

    if len(pairs) < MIN_PAIRS:
        raise ValueError(
            f"删除缺失值后只剩 {len(pairs)} 对完整数据，至少需要 {MIN_PAIRS} 对"
        )

    # 常量列的相关系数没有定义，scipy 只会返回 nan 加一个 warning，得自己拦
    for name in (feature, target):
        if pairs[name].nunique() < 2:
            raise ValueError(f"{name!r} 是常量，没有方差，算不出相关系数")

    correlation, p_value = stats.pearsonr(pairs[feature], pairs[target])
    if not math.isfinite(correlation) or not math.isfinite(p_value):
        raise ValueError(
            f"Pearson 计算得到无效结果（correlation={correlation!r}, p_value={p_value!r}），"
            f"请检查 {feature!r} / {target!r} 的取值"
        )

    return CorrelationAnalysis(
        feature=feature,
        target=target,
        n=len(pairs),
        correlation=float(correlation),
        p_value=float(p_value),
    )


def _describe_correlation(result: CorrelationAnalysis) -> str:
    return (
        f"{result.feature} vs {result.target}: n={result.n} "
        f"correlation={result.correlation:.6f} p={result.p_value:.6f} "
        f"({result.method})"
    )


@tool("correlation_analysis", parse_docstring=True, response_format="content_and_artifact")
def correlation_analysis(dataset: list[dict], feature: str, target: str):
    """计算两个数值变量之间的 Pearson 相关系数。

    对两个字段逐对删除缺失值后计算相关系数和 p 值。只返回数字，不下结论。

    Args:
        dataset: 记录列表，每条至少包含 feature 和 target 两个字段
        feature: 数值字段名，例如 "emotion_score"
        target: 数值字段名，例如 "like_rate"

    Returns:
        参与计算的数据对数、Pearson 相关系数、p 值，结构化结果在 artifact 里
    """
    result = correlate(dataset, feature, target)
    return _describe_correlation(result), result
