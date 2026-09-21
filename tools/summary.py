"""把单个统计结果转成结构化摘要。纯模板，不调 LLM，不重算任何统计量。"""

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.statistics import CorrelationAnalysis, GroupComparison

#: 显著性水平。第一版固定 0.05，且不做多重比较校正
ALPHA = 0.05


class AnalysisSummary(BaseModel):
    """一个统计结果的摘要。数值全部来自入参 artifact，只做展示格式化。"""

    analysis_type: str = Field(description="分析类型：group_comparison 或 correlation")
    feature: str = Field(description="特征字段名")
    target: str = Field(description="目标字段名")
    sample_size: int = Field(description="该分析的样本总数")
    key_result: str = Field(description="数值摘要：各组的量级和差异")
    statistical_result: dict[str, Any] = Field(
        description="原始统计结果，原样保留未经格式化"
    )
    interpretation: str = Field(description="显著性解读，不含因果或趋势措辞")


def summarize_group_comparison(comparison: GroupComparison) -> AnalysisSummary:
    """把分组比较转成摘要。均值、差异、p 值全部直接取入参。"""
    # 比率字段按百分比展示，其余保留数值口径。只影响文本，不动 artifact
    if comparison.target.endswith("_rate"):
        true_text = f"{comparison.true_group.mean:.2%}"
        false_text = f"{comparison.false_group.mean:.2%}"
        difference_text = f"{comparison.mean_difference * 100:.2f} 个百分点"
    else:
        true_text = f"{comparison.true_group.mean:.4f}"
        false_text = f"{comparison.false_group.mean:.4f}"
        difference_text = f"{comparison.mean_difference:.4f}"

    verdict = "达到统计显著" if comparison.p_value < ALPHA else "未达到统计显著"
    return AnalysisSummary(
        analysis_type="group_comparison",
        feature=comparison.feature,
        target=comparison.target,
        # 两组样本数相加只是计总数，不是统计量
        sample_size=comparison.true_group.n + comparison.false_group.n,
        key_result=(
            f"{comparison.feature}=true 组的 {comparison.target} 平均值为 {true_text}，"
            f"false 组为 {false_text}，两组平均差异为 {difference_text}。"
        ),
        statistical_result=comparison.model_dump(),
        interpretation=(
            f"{comparison.method} 的 p 值为 {comparison.p_value:.4f}，"
            f"在 {ALPHA} 显著性水平下{verdict}。"
        ),
    )


def summarize_correlation(correlation: CorrelationAnalysis) -> AnalysisSummary:
    """把相关分析转成摘要。r、p、n 全部直接取入参。"""
    verdict = "达到统计显著" if correlation.p_value < ALPHA else "未达到统计显著"
    return AnalysisSummary(
        analysis_type="correlation",
        feature=correlation.feature,
        target=correlation.target,
        sample_size=correlation.n,
        key_result=(
            f"{correlation.feature} 与 {correlation.target} 的 Pearson 相关系数为 "
            f"{correlation.correlation:.4f}，p={correlation.p_value:.4f}，"
            f"n={correlation.n}。"
        ),
        statistical_result=correlation.model_dump(),
        interpretation=f"在 {ALPHA} 显著性水平下，该相关关系{verdict}。",
    )


@tool(
    "summarize_group_comparison",
    parse_docstring=True,
    response_format="content_and_artifact",
)
def summarize_group_comparison_tool(artifact: GroupComparison):
    """把一次已经做完的分组比较转成结构化摘要。

    只格式化，不重算：传进来的数值原样照抄，不取整、不补算、不改小数位。
    每次比较单独调一次；多个结果需要合起来看时，也一个一个转。

    Args:
        artifact: group_comparison 返回的那个结果，各字段照抄不改

    Returns:
        这一次比较的文字摘要，结构化 AnalysisSummary 在 artifact 里
    """
    summary = summarize_group_comparison(artifact)
    return summary.key_result + summary.interpretation, summary


@tool(
    "summarize_correlation",
    parse_docstring=True,
    response_format="content_and_artifact",
)
def summarize_correlation_tool(artifact: CorrelationAnalysis):
    """把一次已经做完的相关分析转成结构化摘要。

    只格式化，不重算：传进来的数值原样照抄，不取整、不补算、不改小数位。

    Args:
        artifact: correlation_analysis 返回的那个结果，各字段照抄不改

    Returns:
        这一次相关分析的文字摘要，结构化 AnalysisSummary 在 artifact 里
    """
    summary = summarize_correlation(artifact)
    return summary.key_result + summary.interpretation, summary
