

from __future__ import annotations

from ..runtime import Artifact


def build_code_wrapper(code: str) -> str:
    """Wrap user code to capture matplotlib charts as artifacts.

    Matches the Daytona sandbox behavior where plt.show() is intercepted
    and charts are emitted as base64 artifacts.
    """
    return f"""\
import sys
import os

# Monkey-patch matplotlib to capture charts (non-interactive backend)
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # DejaVu Sans 没有 CJK 字形，中文标题/标签会变方框。sans-serif 是个候选
    # 列表，matplotlib 取第一个装了的，所以把中文字体排在前面，DejaVu 兜底。
    matplotlib.rcParams['font.sans-serif'] = [
        'WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'DejaVu Sans'
    ]
    # 负号 U+2212 在中文字体里也常常缺字形，关掉让它用 ASCII 的 '-'
    matplotlib.rcParams['axes.unicode_minus'] = False

    # 只压字形缺失这一类，不碰用户代码自己产生的 warning
    import warnings
    warnings.filterwarnings('ignore', message='Glyph .* missing from font')

    _original_show = plt.show
    _chart_count = [0]

    def _capture_show(*args, **kwargs):
        import io, base64
        _chart_count[0] += 1
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        data = base64.b64encode(buf.read()).decode()
        print(f"__CHART_ARTIFACT__:image/png:chart_{{_chart_count[0]}}.png:{{data}}")
        plt.close('all')

    plt.show = _capture_show
except ImportError:
    pass

# Execute user code
{code}
"""


def extract_artifacts(stdout: str) -> tuple[list[Artifact], str]:
    """Extract __CHART_ARTIFACT__ markers from stdout.

    Returns (artifacts, cleaned_stdout).
    """
    artifacts: list[Artifact] = []
    clean_lines: list[str] = []

    for line in stdout.split("\n"):
        if line.startswith("__CHART_ARTIFACT__:"):
            parts = line.split(":", 3)
            if len(parts) == 4:
                _, mime_type, name, data = parts
                artifacts.append(Artifact(type=mime_type, data=data, name=name))
        else:
            clean_lines.append(line)

    return artifacts, "\n".join(clean_lines)
