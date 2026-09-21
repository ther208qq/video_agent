"""把一条短视频的文案和字幕转成结构化内容特征，供后续统计分析消费。"""

from pydantic import BaseModel, Field
from langchain_core.tools import tool


class ContentFeatures(BaseModel):
    """一条短视频的内容特征。"""

    video_id: str = Field(description="视频 ID，由调用方提供")
    has_hook: bool = Field(description="开头是否存在抓注意力的 hook")
    has_question: bool = Field(description="内容中是否提出问题")
    has_conflict: bool = Field(description="是否存在人物/观点/目标之间的冲突对立")
    has_reversal: bool = Field(description="是否存在预期被反转的转折")
    emotion_score: int = Field(ge=1, le=10, description="整体情绪强度，1=平淡，10=极强")
    question_count: int = Field(ge=0, description="实际提出的问题数量")
    topic: str = Field(description="视频主要主题，简短概括")


CRITERIA = """你在给短视频做内容特征标注。只根据下面给出的文字材料判断，不要脑补画面和声音。

判断标准：
- has_hook：开头（简介首句或字幕开头）是否存在刻意抓注意力的设计，例如直接抛结论、制造悬念、
  点名特定人群、强冲击的陈述。平铺直叙的开场算 false。
- has_question：内容里是否出现提问，反问、设问都算。只有问号但不是在提问（如标题党符号）不算。
- has_conflict：是否存在人物之间、观点之间、目标之间的对立或冲突，例如争吵、对比、竞争、反驳。
  单纯描述一件事算 false。
- has_reversal：是否存在预期被推翻的转折，例如"本以为…结果…"、剧情反转、结论与开头相反。
- emotion_score：整体情绪强度，1 = 完全平铺直叙、没有情绪起伏，10 = 极度强烈（狂喜、暴怒、震惊）。
- question_count：实际提出的问题句数量，没有则填 0，且必须与 has_question 一致
  （计数大于 0 时 has_question 必须为 true，计数为 0 时 has_question 必须为 false）。
- topic：视频主要讲什么，用一句简短中文概括，不超过 20 字。

video_id 由调用方提供，你不需要判断。"""


def extract_features(model, video_id, description="", transcript="", duration_ms=0):
    """调 LLM 抽一条视频的内容特征，返回校验过的 ContentFeatures。"""
    material = [
        f"简介：{description or '（无简介）'}",
        f"字幕：{transcript or '（无字幕）'}",
        f"时长：{duration_ms / 1000:.1f} 秒",
    ]
    prompt = CRITERIA + "\n\n--- 待标注视频 ---\n" + "\n".join(material)

    # temperature=0：特征抽取要可复现，否则同一输入两次跑出来的特征会漂。
    # 必须用 model_copy，model.bind(temperature=...) 的参数传不进请求体（实测无效）
    structured = model.model_copy(update={"temperature": 0}).with_structured_output(ContentFeatures)
    features = structured.invoke(prompt)
    # video_id 是调用方的输入，不交给 LLM 猜；数据集里是 int64，统一成 str
    features.video_id = str(video_id)
    return features


def _summary(features: ContentFeatures) -> str:
    flags = [
        name.removeprefix("has_").replace("_", " ")
        for name in ("has_hook", "has_question", "has_conflict", "has_reversal")
        if getattr(features, name)
    ]
    return (f"{features.video_id}: topic={features.topic} emotion={features.emotion_score} "
            f"questions={features.question_count} 命中特征={flags or '无'}")


def create_analyze_content_tool(model):
    """构造 analyze_content 工具；LLM 结果由 ContentFeatures 校验，不手工解析 JSON。"""

    @tool("analyze_content", parse_docstring=True, response_format="content_and_artifact")
    def analyze_content(video_id: str, description: str = "", transcript: str = "",
                        duration_ms: int = 0):
        """分析一条短视频的简介与字幕，抽取结构化内容特征。

        Args:
            video_id: 视频 ID，原样回填到结果里
            description: 视频简介，没有时传空字符串
            transcript: 视频字幕全文，没有字幕时传空字符串
            duration_ms: 视频时长，单位毫秒，未知传 0

        Returns:
            内容特征摘要，结构化结果在 artifact 里
        """
        features = extract_features(model, video_id, description, transcript, duration_ms)
        return _summary(features), features

    return analyze_content
