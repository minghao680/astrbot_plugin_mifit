"""把手环数据画成图。

不用 seaborn / xkcd 那套花哨风格，中文字体配手绘风会糊成一团。
睡眠画堆叠横条（深/浅/REM/醒），步数和心率画柱和折线。

matplotlib 是可选依赖：没装就返回 None，上层退回纯文本，不至于整个插件挂掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Microsoft YaHei",
    "PingFang SC",
)

STAGE_COLORS = (
    ("deep_min", "深睡", "#2b4a8b"),
    ("light_min", "浅睡", "#4f8fd6"),
    ("rem_min", "REM", "#8f6fd0"),
    ("awake_min", "清醒", "#d9c9a3"),
)


def available() -> bool:
    try:
        import matplotlib  # noqa: F401

        return True
    except Exception:
        return False


def _setup() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt

    names = {f.name for f in font_manager.fontManager.ttflist}
    picked = [n for n in _FONT_CANDIDATES if n in names]
    if picked:
        plt.rcParams["font.sans-serif"] = picked + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "#f6f7f9"
    plt.rcParams["axes.facecolor"] = "#f6f7f9"
    plt.rcParams["axes.edgecolor"] = "#c9ced6"
    plt.rcParams["axes.labelcolor"] = "#2b2f36"
    plt.rcParams["text.color"] = "#2b2f36"
    plt.rcParams["xtick.color"] = "#5b6270"
    plt.rcParams["ytick.color"] = "#5b6270"
    return plt


def _footer(fig: Any, text: str) -> None:
    """在图表底部压一行汇总，信息密度上去了又不占版面。"""
    if not text:
        return
    fig.text(0.012, -0.02, text, fontsize=8.5, color="#6b7280", ha="left", va="top")


def _stat(values: list[int], prefix: str = "") -> str:
    vals = [v for v in values if v]
    if not vals:
        return ""
    avg = sum(vals) / len(vals)
    lead = f"{prefix}合计" if prefix else "合计"
    return f"{lead}{sum(vals)} 日均{avg:.1f} 高{max(vals)} 低{min(vals)}"


def _save(fig: Any, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    import matplotlib.pyplot as plt

    plt.close(fig)
    return out


def _short(value: Any) -> str:
    text = str(value or "")
    return text[5:10] if len(text) >= 10 else text


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def draw_sleep(rows: list[dict[str, Any]], out: Path, title: str = "") -> Path | None:
    plt = _setup()
    # 后端已按日期正序，这里翻成最新在前，倒序画配合 invert_yaxis 就是最新在顶部
    rows = [r for r in reversed([r for r in rows if r.get("date")])]
    if not rows:
        return None

    labels = [_short(r["date"]) for r in rows]
    positions = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(7.2, 0.52 * len(rows) + 1.5))
    left = [0] * len(rows)
    for key, name, color in STAGE_COLORS:
        values = [_to_int(r.get(key)) for r in rows]
        if not any(values):
            continue
        ax.barh(positions, values, left=left, color=color, label=name, height=0.62,
                edgecolor="white", linewidth=0.6)
        left = [a + b for a, b in zip(left, values)]

    for i, row in enumerate(rows):
        total = _to_int(row.get("asleep_min"))
        if total:
            h, m = divmod(total, 60)
            ax.text(left[i] + 6, i, f"{h}h{m:02d}m" if h else f"{m}m",
                    va="center", fontsize=9, color="#3a4048")

    ax.invert_yaxis()
    ax.set_yticks(positions)
    ax.set_yticklabels(labels)
    ax.set_xlabel("分钟")
    ax.set_xlim(0, max(left + [1]) * 1.14)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title or "睡眠结构", fontsize=12, loc="left", pad=26)
    handles, labels_ = ax.get_legend_handles_labels()
    if handles:
        # 图例放标题下面一行，别塞进柱子区里压着数据
        ax.legend(
            handles,
            labels_,
            loc="lower left",
            bbox_to_anchor=(0, 1.0),
            ncol=len(handles),
            frameon=False,
            fontsize=9,
            handlelength=1.2,
            columnspacing=1.4,
        )
    # 底部汇总：平均时长 + 深睡占比
    totals = [_to_int(r.get("asleep_min")) for r in rows]
    deeps = [_to_int(r.get("deep_min")) for r in rows]
    if any(totals):
        avg = sum(totals) / len([t for t in totals if t])
        deep_sum = sum(deeps)
        deep_pct = deep_sum / sum(totals) * 100 if sum(totals) else 0
        h, m = divmod(int(avg), 60)
        _footer(fig, f"平均每晚{h}h{m:02d}m 深睡占比{deep_pct:.0f}% 最长{max(totals)}分 最短{min(totals)}分")
    return _save(fig, out)


def draw_stacked(
    rows: list[dict[str, Any]],
    out: Path,
    series: list[tuple[str, str, str]],
    title: str,
    unit: str = "",
    line_key: str = "",
    line_name: str = "",
) -> Path | None:
    """堆叠柱，可选再叠一条折线（双轴），一次把构成和累计都讲清楚。"""
    plt = _setup()
    rows = [r for r in rows if r.get("date")]
    if not rows:
        return None

    labels = [_short(r["date"]) for r in rows]
    positions = list(range(len(labels)))

    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    bottom = [0] * len(rows)
    for key, name, color in series:
        values = [_to_int(r.get(key)) for r in rows]
        if not any(values):
            continue
        ax.bar(positions, values, bottom=bottom, color=color, label=name,
               width=0.58, edgecolor="white", linewidth=0.6)
        bottom = [a + b for a, b in zip(bottom, values)]
    totals = bottom

    top = max(totals + [1]) * 1.35
    for pos, total in zip(positions, totals):
        if not total:
            continue
        if line_key:
            # 有折线时数字压在柱子内部，避免和折线的标签撞一起
            ax.text(pos, total * 0.5, f"{total}{unit}", ha="center", va="center",
                    fontsize=9, color="white", fontweight="bold")
        else:
            ax.text(pos, total, f"{total}{unit}", ha="center", va="bottom",
                    fontsize=8.5, color="#3a4048")

    if line_key and any(r.get(line_key) is not None for r in rows):
        ax2 = ax.twinx()
        pts = [r.get(line_key) if r.get(line_key) is not None else float("nan")
               for r in rows]
        ax2.plot(positions, pts, color="#c0392b", marker="o", markersize=4,
                 linewidth=1.8, label=line_name or line_key)
        for pos, value in zip(positions, pts):
            if value == value:
                ax2.annotate(f"{int(value)}", (pos, value), textcoords="offset points",
                             xytext=(0, 7), ha="center", fontsize=8, color="#c0392b")
        ax2.set_ylabel(line_name, color="#c0392b", fontsize=9)
        ax2.tick_params(axis="y", colors="#c0392b", labelsize=8)
        ax2.spines["top"].set_visible(False)
        line_max = max([v for v in pts if v == v] + [1])
        ax2.set_ylim(0, line_max * 1.25)
        handles1, labels1 = ax.get_legend_handles_labels()
        handles2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(handles1 + handles2, labels1 + labels2, loc="lower left",
                  bbox_to_anchor=(0, 1.0), ncol=len(handles1) + len(handles2),
                  frameon=False, fontsize=9, handlelength=1.2, columnspacing=1.4)

    ax.set_ylim(0, top)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e2e6ec", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, fontsize=12, loc="left", pad=26)
    _footer(fig, _stat(totals, prefix="当日"))
    return _save(fig, out)


def draw_bar(
    rows: list[dict[str, Any]],
    out: Path,
    key: str,
    title: str,
    unit: str = "",
    color: str = "#3f7fd0",
    target: float | None = None,
    target_name: str = "目标",
    extra: tuple[str, str, str, float] | None = None,
) -> Path | None:
    """单值柱状图。可以叠一条目标线，也可以挂一个次级量在底部当标签。

    extra = (字段, 名称, 单位, 换算除数)，用来把距离之类塞进柱底。
    """
    plt = _setup()
    rows = [r for r in rows if r.get("date")]
    if not rows:
        return None

    labels = [_short(r["date"]) for r in rows]
    values = [_to_int(r.get(key)) for r in rows]
    # 日期形如 11-01，matplotlib 会当成浮点数解析，所以显式用序号当横坐标
    positions = list(range(len(labels)))

    ceiling = max(values + [1]) * 1.22
    if target:
        ceiling = max(ceiling, target * 1.15)
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    bars = ax.bar(positions, values, color=color, width=0.55)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{value}{unit}", ha="center", va="bottom", fontsize=8.5,
                color="#3a4048")

    if extra:
        field, ename, eunit, divisor = extra
        texts = []
        for row in rows:
            raw = row.get(field)
            if raw is None:
                texts.append("")
            else:
                texts.append(f"{float(raw) / divisor:.1f}{eunit}")
        if any(texts):
            tallest = max(values + [1])
            for pos, text, value in zip(positions, texts, values):
                # 柱子太矮放不下白字，就挪到柱顶外面
                if not text or value <= 0:
                    continue
                if value > tallest * 0.16:
                    ax.annotate(text, (pos, value * 0.5), ha="center",
                                fontsize=8, color="white")
                else:
                    ax.annotate(text, (pos, value), textcoords="offset points",
                                xytext=(0, 16), ha="center", fontsize=8,
                                color="#6b7280")

    if target:
        ax.axhline(target, color="#c0392b", linestyle="--", linewidth=1.2)
        ax.annotate(f"{target_name} {target:g}", (len(values) - 0.5, target),
                    fontsize=8.5, color="#c0392b", va="center", ha="left",
                    xytext=(6, 0), textcoords="offset points", annotation_clip=False)

    ax.set_ylim(0, ceiling)
    if target:
        ax.set_xlim(-0.6, len(values) - 0.4 + max(1.2, len(values) * 0.16))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title, fontsize=12, loc="left", pad=12)
    ax.grid(axis="y", color="#e2e6ec", linewidth=0.8)
    ax.set_axisbelow(True)
    _footer(fig, _stat(values))
    return _save(fig, out)


def draw_heart(rows: list[dict[str, Any]], out: Path) -> Path | None:
    plt = _setup()
    # 后端已按日期正序，直接画横轴就是左旧右新
    rows = [r for r in rows if r.get("date")]
    if not rows:
        return None

    labels = [_short(r["date"]) for r in rows]
    positions = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    series = (
        ("max_bpm", "最高", "#d4645a", "-"),
        ("avg_bpm", "平均", "#3f7fd0", "-"),
        ("resting_bpm", "静息", "#5fa86b", "--"),
    )
    drawn = False
    for key, name, color, style in series:
        values = [r.get(key) for r in rows]
        if not any(v is not None for v in values):
            continue
        pts = [v if v is not None else float("nan") for v in values]
        ax.plot(positions, pts, label=name, color=color, linestyle=style,
                marker="o", markersize=3.5, linewidth=1.6)
        drawn = True

    ax.set_xticks(positions)
    ax.set_xticklabels(labels)


    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("心率", fontsize=12, loc="left", pad=26)
    ax.grid(axis="y", color="#e2e6ec", linewidth=0.8)
    ax.set_axisbelow(True)
    if drawn:
        # 同理，放到标题下方，避免落在数据线中间
        ax.legend(
            loc="lower left",
            bbox_to_anchor=(0, 1.0),
            ncol=3,
            frameon=False,
            fontsize=9,
            handlelength=1.6,
            columnspacing=1.4,
        )
    avgs = [r.get("avg_bpm") for r in rows if r.get("avg_bpm") is not None]
    rhrs = [r.get("resting_bpm") for r in rows if r.get("resting_bpm") is not None]
    if avgs:
        bits = [f"平均心率{sum(avgs) / len(avgs):.0f}"]
        if rhrs:
            bits.append(f"静息均{sum(rhrs) / len(rhrs):.0f}")
        bits.append(f"最高{max(avgs)} 最低{min(avgs)}")
        _footer(fig, " ".join(bits))
    return _save(fig, out)


def draw_sleep_timeline(
    rows: list[dict[str, Any]],
    out: Path,
    days: int = 7,
    title: str = "",
) -> Path | None:
    """睡眠分期时间轴：每一行一晚，色块是深/浅/REM，横轴是钟点。

    比堆叠柱更能看出作息漂移——几点入睡、几点醒、中间醒没醒。
    """
    plt = _setup()
    from datetime import datetime

    picks = [r for r in rows if r.get("date") and r.get("items")]
    if not picks:
        return None
    picks = picks[-days:]
    labels: list[str] = []

    fig, ax = plt.subplots(figsize=(7.8, 0.55 * len(picks) + 1.6))
    state_color = {1: "#d9c9a3", 2: "#2b4a8b", 3: "#4f8fd6", 4: "#8f6fd0", 5: "#c9b6e8"}
    handles: dict[str, Any] = {}

    for i, row in enumerate(picks):
        bedtime = row.get("bedtime")
        base = int(bedtime) if bedtime else None
        if base is None:
            items = row.get("items") or []
            base = int(items[0]["start_time"]) if items else None
        if base is None:
            continue
        for item in row.get("items") or []:
            state = item.get("state")
            start, end = item.get("start_time"), item.get("end_time")
            if state is None or start is None or end is None:
                continue
            x0 = (int(start) - base) / 3600.0
            width = (int(end) - int(start)) / 3600.0
            name = STAGE_LABELS.get(state, f"state{state}")
            bar = ax.barh(i, width, left=x0, height=0.6,
                          color=state_color.get(state, "#999999"),
                          edgecolor="white", linewidth=0.4)
            if name not in handles:
                handles[name] = bar[0]

        # 醒来钟点标在柱子右端，入睡钟点并进左侧标签
        wake = row.get("wake_up_time")
        if wake:
            ax.text((int(wake) - base) / 3600.0, i,
                    f" {datetime.fromtimestamp(int(wake)).strftime('%H:%M')}",
                    va="center", fontsize=8, color="#6b7280")
        labels.append(
            f"{_short(row['date'])} {datetime.fromtimestamp(base).strftime('%H:%M')}"
        )

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("距入睡小时数")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title or "睡眠分期时间轴", fontsize=12, loc="left", pad=26)
    if handles:
        ax.legend(handles.values(), handles.keys(), loc="lower left",
                  bbox_to_anchor=(0, 1.0), ncol=len(handles), frameon=False,
                  fontsize=9, handlelength=1.2, columnspacing=1.4)
    _footer(fig, "标签左侧为入睡钟点，柱尾为醒来钟点")
    return _save(fig, out)


STAGE_LABELS = {1: "清醒", 2: "深睡", 3: "浅睡", 4: "REM", 5: "小睡"}


def draw_stand_windows(
    rows: list[dict[str, Any]],
    out: Path,
    title: str = "",
) -> Path | None:
    """有效站立时间窗：横轴一天 24 小时，每条是一段活动区间。

    能直接看出一天里哪几个钟头真的动过，比一个「站立 6 次」的数字有用。
    """
    plt = _setup()
    from datetime import datetime

    spans: list[tuple[str, float, float]] = []
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        start, end = value.get("start_time"), value.get("end_time")
        if start is None or end is None:
            continue
        day = datetime.fromtimestamp(int(start))
        begin = day.hour + day.minute / 60.0
        length = max(0.0, (int(end) - int(start)) / 3600.0)
        spans.append((day.strftime("%m-%d"), begin, length))
    if not spans:
        return None

    spans.sort(key=lambda s: (s[0], s[1]))
    day_names = sorted({s[0] for s in spans}, reverse=True)
    index = {name: i for i, name in enumerate(day_names)}

    fig, ax = plt.subplots(figsize=(7.6, 0.5 * len(day_names) + 1.8))
    for name, begin, length in spans:
        ax.barh(index[name], length, left=begin, height=0.5,
                color="#8f6fd0", edgecolor="white", linewidth=0.5)

    ax.set_yticks(range(len(day_names)))
    ax.set_yticklabels(day_names)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title or "有效站立时段", fontsize=12, loc="left", pad=12)
    ax.grid(axis="x", color="#e2e6ec", linewidth=0.8)
    ax.set_axisbelow(True)
    _footer(fig, f"共 {len(spans)} 段，淡紫块为站立活动区间")
    return _save(fig, out)


def draw_hr_points(
    rows: list[dict[str, Any]],
    out: Path,
    title: str = "",
) -> Path | None:
    """单日心率散点：横轴是钟点，纵轴 bpm，顺带画出静息线和区间均值。"""
    plt = _setup()
    from datetime import datetime

    points: list[tuple[float, int]] = []
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        bpm, ts = value.get("bpm"), value.get("time")
        if bpm is None or ts is None:
            continue
        stamp = datetime.fromtimestamp(int(ts))
        points.append((stamp.hour + stamp.minute / 60.0, int(bpm)))
    if not points:
        return None

    points.sort()
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    ax.scatter(xs, ys, s=7, color="#3f7fd0", alpha=0.62, edgecolors="none")

    avg = sum(ys) / len(ys)
    ax.axhline(avg, color="#d4845a", linestyle="--", linewidth=1.1)
    ax.annotate(f"均 {avg:.0f}", (24, avg), fontsize=8.5, color="#d4845a",
                va="center", ha="left", xytext=(4, 0), textcoords="offset points",
                annotation_clip=False)

    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(title or "心率散点", fontsize=12, loc="left", pad=12)
    ax.grid(axis="y", color="#e2e6ec", linewidth=0.8)
    ax.set_axisbelow(True)
    _footer(fig, f"{len(points)} 个采样点 均{avg:.0f} 高{max(ys)} 低{min(ys)}")
    return _save(fig, out)
