"""把已经算好的统计结果转成前端能直接画的图描述。只搬运，不重新计算任何统计量。"""

from typing import Any

from pydantic import BaseModel, Field

from tools.statistics import GroupComparison


class BarSeries(BaseModel):
    """柱状图里的一根柱子。"""

    name: str = Field(description="该柱代表的组名")
    value: float = Field(description="该组的均值")
    n: int = Field(description="该组样本数")


class VisualizationSpec(BaseModel):
    """一张图的完整描述，交给前端渲染。"""

    chart_type: str = Field(default="bar", description="图表类型")
    title: str = Field(description="图表标题")
    x_label: str = Field(description="横轴含义")
    y_label: str = Field(description="纵轴含义")
    series: list[BarSeries] = Field(description="要画的柱子，按顺序")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="原始统计结果，原样带过来供报告引用"
    )


def visualize_group_comparison(comparison: GroupComparison) -> VisualizationSpec:
    """把 GroupComparison 转成双柱柱状图描述。均值、样本数都直接取自入参。"""
    pairs = (
        (f"{comparison.feature} = true", comparison.true_group),
        (f"{comparison.feature} = false", comparison.false_group),
    )
    return VisualizationSpec(
        chart_type="bar",
        title=f"{comparison.feature} 分组下 {comparison.target} 的均值对比",
        x_label=comparison.feature,
        y_label=comparison.target,
        series=[
            BarSeries(name=name, value=group.mean, n=group.n) for name, group in pairs
        ],
        metadata={
            "p_value": comparison.p_value,
            "mean_difference": comparison.mean_difference,
            "t_statistic": comparison.t_statistic,
            "method": comparison.method,
            # 中位数只在原始结果里，柱子上画不出来，留着给报告用
            "true_group": comparison.true_group.model_dump(),
            "false_group": comparison.false_group.model_dump(),
        },
    )
