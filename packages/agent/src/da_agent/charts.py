"""图表生成（5.1 四件套的"图"）：零依赖 SVG 渲染。

确定性规则：二列结果（类别+数值）出条形图；首列像日期的出折线图。
SVG 内嵌于回答与报告，Web 端直接渲染。
"""

from __future__ import annotations

from da_types import QueryResult

MAX_BARS = 12
W, H, PAD_L, PAD_B = 640, 300, 120, 28
PALETTE = ["#4da3ff", "#5fd0a5", "#f2b04e", "#e0679a", "#9b7ded", "#6ac7de"]


def chartable(result: QueryResult) -> str | None:
    """返回 'bar' / 'line' / None。"""
    if len(result.columns) != 2 or not (1 < len(result.rows) <= 60):
        return None
    try:
        [float(r[1]) for r in result.rows if r[1] is not None]
    except (TypeError, ValueError):
        return None
    first = str(result.rows[0][0])
    is_date = len(first) >= 8 and first[:4].isdigit() and ("-" in first or "/" in first)
    return "line" if is_date else ("bar" if len(result.rows) <= MAX_BARS else "line")


def render_chart(result: QueryResult, title: str = "") -> str | None:
    kind = chartable(result)
    if kind is None:
        return None
    rows = [(str(r[0]), float(r[1]) if r[1] is not None else 0.0) for r in result.rows]
    values = [v for _, v in rows]
    vmax = max(values + [0]) or 1.0
    plot_w, plot_h = W - PAD_L - 20, H - PAD_B - 30

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="sans-serif" font-size="12">',
        f'<text x="{PAD_L}" y="18" font-size="14" fill="#333">{_esc(title)}</text>',
    ]
    if kind == "bar":
        bar_h = plot_h / len(rows)
        for i, (label, v) in enumerate(rows):
            y = 30 + i * bar_h
            w = plot_w * v / vmax
            color = PALETTE[i % len(PALETTE)]
            parts.append(
                f'<rect x="{PAD_L}" y="{y + 2:.1f}" width="{w:.1f}" '
                f'height="{bar_h - 6:.1f}" fill="{color}" rx="3"/>'
            )
            parts.append(
                f'<text x="{PAD_L - 6}" y="{y + bar_h / 2 + 4:.1f}" '
                f'text-anchor="end" fill="#555">{_esc(label[:14])}</text>'
            )
            parts.append(
                f'<text x="{PAD_L + w + 4:.1f}" y="{y + bar_h / 2 + 4:.1f}" '
                f'fill="#333">{_fmt(v)}</text>'
            )
    else:
        step = plot_w / max(len(rows) - 1, 1)
        points = " ".join(
            f"{PAD_L + i * step:.1f},{30 + plot_h - plot_h * v / vmax:.1f}"
            for i, (_, v) in enumerate(rows)
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{PALETTE[0]}" '
            f'stroke-width="2"/>'
        )
        n_ticks = min(6, len(rows))
        for t in range(n_ticks):
            i = round(t * (len(rows) - 1) / max(n_ticks - 1, 1))
            x = PAD_L + i * step
            parts.append(
                f'<text x="{x:.1f}" y="{H - 8}" text-anchor="middle" '
                f'fill="#555">{_esc(rows[i][0][-5:])}</text>'
            )
        parts.append(
            f'<text x="{PAD_L}" y="{H - 8}" fill="#999" text-anchor="end">'
            f"max {_fmt(vmax)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
