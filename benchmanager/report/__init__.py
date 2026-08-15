"""レポート生成(Phase 1: Plotly 自己完結 HTML)。"""

from .html import Series, SeriesPoint, build_figure, collect_series, default_output_name, write_report

__all__ = [
    "Series",
    "SeriesPoint",
    "build_figure",
    "collect_series",
    "default_output_name",
    "write_report",
]
