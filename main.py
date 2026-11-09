"""小米运动健康（Mi Fitness）数据插件。

扫码登录一次，之后从小米云读手环同步上去的数据：睡眠、步数、心率、血氧、压力。
底层用 mifit（非官方逆向），小米改接口就可能失效，所以这里所有失败都老实报出来。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from . import backend, charts

TOOL_PROMPT = """
查手环数据（睡眠、步数、心率、血氧、压力）用 mifit_health，别瞎猜。
没登录会返回未登录，这时用 mifit_login 发二维码给用户扫。
日期一律用 YYYY-MM-DD，days 是回看天数。
""".strip()

METRICS: dict[str, str] = {
    "sleep": "睡眠",
    "steps": "步数",
    "heart": "心率",
    "spo2": "血氧",
    "stress": "压力",
    "activity": "活动",
    "consumption": "热量",
    "calories": "活动卡路里",
    "stand": "有效站立",
    "intensity": "中高强度",
    "goal": "目标达成",
    "score": "睡眠评分",
    "vitality": "元气值",
    "breath": "睡眠呼吸质量",
    "stage": "睡眠分期",
    "hrsample": "心率采样",
    "standwin": "站立时段",
}

METRIC_ALIASES: dict[str, str] = {
    "睡眠": "sleep",
    "睡觉": "sleep",
    "步数": "steps",
    "走路": "steps",
    "心率": "heart",
    "心跳": "heart",
    "血氧": "spo2",
    "压力": "stress",
    "活动": "activity",
    "热量": "consumption",
    "卡路里": "calories",
    "站立": "stand",
    "中高强度": "intensity",
    "目标": "goal",
    "评分": "score",
    "元气": "vitality",
    "元气值": "vitality",
    "呼吸": "breath",
    "呼吸质量": "breath",
    "分期": "stage",
    "睡眠分期": "stage",
    "采样": "hrsample",
    "心率采样": "hrsample",
    "站立时段": "standwin",
}


def _hm(minutes: Any) -> str:
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "0"
    hours, mins = divmod(total, 60)
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def _num(value: Any, unit: str = "") -> str:
    if value is None:
        return "-"
    return f"{value}{unit}"


def _short_date(value: Any) -> str:
    text = str(value or "")
    return text[5:10] if len(text) >= 10 else text


def _fmt_sleep(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        parts = [f"{date} 睡{_hm(row.get('asleep_min'))}"]
        detail = []
        if row.get("deep_min") is not None:
            detail.append(f"深{_hm(row.get('deep_min'))}")
        if row.get("light_min") is not None:
            detail.append(f"浅{_hm(row.get('light_min'))}")
        if row.get("rem_min") is not None:
            detail.append(f"REM{_hm(row.get('rem_min'))}")
        if row.get("awake_min"):
            detail.append(f"醒{_hm(row.get('awake_min'))}")
        if detail:
            parts.append(" ".join(detail))
        hr = row.get("avg_hr_bpm")
        if hr:
            parts.append(f"均心率{hr}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _fmt_steps(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        bits = [f"{date} {_num(row.get('steps'), '步')}"]
        if row.get("distance_m") is not None:
            bits.append(f"{int(row['distance_m']) / 1000:.1f}km")
        if row.get("step_calories") is not None:
            bits.append(f"{row['step_calories']}kcal")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _fmt_heart(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        lines.append(
            f"{date} 均{_num(row.get('avg_bpm'))} 静息{_num(row.get('resting_bpm'))} "
            f"高{_num(row.get('max_bpm'))} 低{_num(row.get('min_bpm'))}"
        )
    return "\n".join(lines)


def _fmt_spo2(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        lines.append(
            f"{date} 均{_num(row.get('avg_spo2_pct'), '%')} "
            f"低{_num(row.get('min_spo2_pct'), '%')}"
        )
    return "\n".join(lines)


def _fmt_goal(rows: list[dict[str, Any]]) -> str:
    field_names = {1: "步数", 2: "活动千卡", 3: "站立", 4: "中高强度分钟"}
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        bits = []
        for item in row.get("goal_items") or []:
            if not isinstance(item, dict):
                continue
            name = field_names.get(item.get("field"), f"field{item.get('field')}")
            bits.append(f"{name} {item.get('achieved_value')}/{item.get('target_value')}")
        stand = row.get("stand_goal")
        if isinstance(stand, dict):
            bits.append(f"站立 {stand.get('achieved_value')}/{stand.get('target_value')}")
        lines.append(f"{date} " + " ".join(bits))
    return "\n".join(lines)


def _fmt_score(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        bits = []
        score = row.get("sleep_score")
        if score is not None:
            bits.append(f"睡眠评分{score}")
        long_eval = row.get("long_sleep_evaluation")
        if long_eval is not None:
            bits.append(f"长睡评估{long_eval}")
        # 一天可能有多段（主睡眠 + 午睡），取时长最长的当主睡眠
        segs = [x for x in (row.get("segment_details") or []) if isinstance(x, dict)]
        if segs:
            main = max(segs, key=lambda x: x.get("duration") or 0)
            bed, wake = main.get("bedtime"), main.get("wake_up_time")
            if bed and wake:
                bits.append(
                    f"{datetime.fromtimestamp(int(bed)).strftime('%H:%M')}"
                    f"-{datetime.fromtimestamp(int(wake)).strftime('%H:%M')}"
                )
            if len(segs) > 1:
                bits.append(f"另有{len(segs) - 1}段小睡")
        lines.append(f"{date} " + " ".join(bits))
    return "\n".join(lines)


def _fmt_vitality(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        bits = []
        total = row.get("latest_accumulated_vitality")
        if total is not None:
            bits.append(f"累计{total}")
        low = row.get("daily_low_intensity_vitality")
        med = row.get("daily_medium_intensity_vitality")
        high = row.get("daily_high_intensity_vitality")
        if any(v is not None for v in (low, med, high)):
            bits.append(f"当日 低{low or 0}/中{med or 0}/高{high or 0}")
        suggest = row.get("suggested_activity_duration")
        if suggest is not None:
            bits.append(f"建议运动{suggest}分")
        lines.append(f"{date} " + " ".join(bits))
    return "\n".join(lines)


def _fmt_breath(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        date = _short_date(row.get("date"))
        quality = value.get("breath_quality")
        if quality is None:
            continue
        bits = [f"{date} 呼吸质量{quality}"]
        if value.get("sleep_algorithm_version"):
            bits.append(f"算法v{value['sleep_algorithm_version']}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


# 分期 state：2 深睡 3 浅睡 4 REM
STAGE_NAMES = {1: "清醒", 2: "深睡", 3: "浅睡", 4: "REM", 5: "小睡"}


def _fmt_stage(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        items = value.get("items") or []
        if not items:
            continue
        date = _short_date(row.get("date"))
        agg: dict[int, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            state = item.get("state")
            start, end = item.get("start_time"), item.get("end_time")
            if state is None or start is None or end is None:
                continue
            agg[state] = agg.get(state, 0) + max(0, int(end) - int(start)) // 60
        bits = [
            f"{STAGE_NAMES.get(state, f'state{state}')}{_hm(minutes)}"
            for state, minutes in sorted(agg.items())
            if minutes > 0
        ]
        lines.append(f"{date} 共{len(items)}段 " + " ".join(bits))
    return "\n".join(lines)


def _fmt_hrsample(rows: list[dict[str, Any]]) -> str:
    points = []
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        bpm, ts = value.get("bpm"), value.get("time")
        if bpm is None or ts is None:
            continue
        points.append((int(ts), int(bpm)))
    if not points:
        return "没有采样点"
    points.sort()
    bpms = [bpm for _, bpm in points]
    lines = [
        f"{len(points)} 个采样点，均 {sum(bpms) // len(bpms)}，"
        f"高 {max(bpms)}，低 {min(bpms)}"
    ]
    for ts, bpm in points:
        lines.append(f"{datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')} {bpm}")
    return "\n".join(lines[:41])


def _fmt_standwin(rows: list[dict[str, Any]]) -> str:
    lines = []
    rows = sorted(rows, key=lambda r: (r.get("value") or {}).get("start_time") or 0)
    for row in rows:
        value = row.get("value") if isinstance(row.get("value"), dict) else row
        start, end = value.get("start_time"), value.get("end_time")
        if start is None or end is None:
            continue
        lines.append(
            f"{datetime.fromtimestamp(int(start)).strftime('%m-%d %H:%M')}"
            f"-{datetime.fromtimestamp(int(end)).strftime('%H:%M')}"
        )
    if not lines:
        return "没有有效站立时段"
    return f"{len(lines)} 段有效站立\n" + "\n".join(lines)


def _fmt_generic(rows: list[dict[str, Any]], unit: str = "") -> str:
    lines = []
    for row in rows:
        date = _short_date(row.get("date"))
        rest = [
            f"{k}={v}"
            for k, v in row.items()
            if k not in {"date", "epoch_s", "sessions"} and v is not None
        ]
        lines.append(f"{date} " + " ".join(rest) + unit)
    return "\n".join(lines)


@register(
    "astrbot_plugin_mifit",
    "明毫",
    "小米运动健康数据：睡眠 / 步数 / 心率 / 血氧 / 压力",
    "0.1.0",
)
class MiFitPlugin(Star):
    def __init__(self, context: Context, config: Any | None = None) -> None:
        super().__init__(context)
        self.config = config
        self.tz = str(self._cfg("timezone", "Asia/Shanghai") or "Asia/Shanghai")
        self.default_days = int(self._cfg("default_days", 7) or 7)
        self.max_rows = int(self._cfg("max_rows", 30) or 30)
        self._session_path = backend.session_path(str(self._cfg("session_path", "") or ""))
        backend.ensure_dirs()
        backend.set_timezone(self.tz)

    def _cfg(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    async def initialize(self) -> None:
        state = "已登录" if backend.has_session(self._session_path) else "未登录"
        logger.info(f"[mifit] 就绪，会话{state}，时区 {self.tz}")

    @staticmethod
    def _normalize_metric(metric: str) -> str:
        key = (metric or "sleep").strip().lower()
        return METRIC_ALIASES.get(key, key)

    @llm_tool(name="mifit_login")
    async def mifit_login(self, event: AstrMessageEvent) -> str:
        """登录小米运动健康账号。

        会给用户发一张二维码图，让用户用小米运动健康 App 或小米账号扫码确认。
        已经登录过的话一般不用再来一次，session 会自动续。
        """
        try:
            info = backend.start_login(self._session_path)
        except Exception as exc:
            logger.exception("[mifit] 请求二维码失败")
            return f"取登录二维码失败：{type(exc).__name__}: {exc}"

        qr_path: Path = info["qr_path"]
        try:
            await event.send(event.chain_result([Image.fromFileSystem(str(qr_path))]))
        except Exception as exc:
            logger.warning(f"[mifit] 二维码图发送失败: {exc}")
            return (
                "二维码图没发出去，直接在浏览器打开这个链接也能登录：\n"
                f"{info['login_url']}\n登录令牌 {info['token']}"
            )
        return (
            "二维码发过去了，用小米运动健康 App 里自带的扫一扫，别拿微信扫，"
            "微信只会把码当链接打开。真不行就用手机浏览器打开这个地址，"
            f"页面上那个码再拿 App 扫：{info['login_url']}\n扫完告诉我，登录令牌 {info['token']}"
        )

    @llm_tool(name="mifit_login_status")
    async def mifit_login_status(self, event: AstrMessageEvent, token: str) -> str:
        """查扫码登录的进度。

        token 是 mifit_login 返回的登录令牌。state 为 waiting/ok/failed/missing。
        """
        state, error = backend.login_state(token)
        if state == "ok":
            return "登录成功，会话已保存，可以查数据了"
        if state == "waiting":
            return "还在等确认，手机上点完了吗"
        if state == "failed":
            return f"登录失败：{error}"
        return "这个登录令牌不认识了，重新发一次二维码吧"

    @llm_tool(name="mifit_health")
    async def mifit_health(
        self,
        event: AstrMessageEvent,
        metric: str = "sleep",
        days: int = 7,
        end_date: str = "",
    ) -> str:
        """读手环数据。

        metric 可选 sleep / steps / heart / spo2 / stress / activity / consumption。
        days 是回看天数，end_date 是结束日期 YYYY-MM-DD，留空表示到今天。
        """
        if not backend.has_session(self._session_path):
            return "还没登录小米账号，先用 mifit_login 扫码"

        key = self._normalize_metric(metric)
        if key not in METRICS:
            return f"没有 {metric} 这种数据，能查的是 " + " / ".join(METRICS)

        days = max(1, min(int(days or self.default_days), self.max_rows))
        try:
            start, end = backend.date_range(days, end_date or None)
        except ValueError:
            return "日期格式不对，要 YYYY-MM-DD"

        try:
            rows = await self._load_rows(key, start, end)
        except Exception as exc:
            logger.exception("[mifit] 读数据失败")
            name = type(exc).__name__
            if name in {"AuthError", "RenewalError"}:
                return f"会话失效了（{name}），得重新用 mifit_login 扫码"
            return f"读数据失败：{name}: {exc}"

        if not rows:
            return f"{start} 到 {end} 的{METRICS[key]}没有记录，手环可能没同步到云"

        formatter = {
            "sleep": _fmt_sleep,
            "steps": _fmt_steps,
            "heart": _fmt_heart,
            "spo2": _fmt_spo2,
            "goal": _fmt_goal,
            "score": _fmt_score,
            "vitality": _fmt_vitality,
            "breath": _fmt_breath,
            "stage": _fmt_stage,
            "hrsample": _fmt_hrsample,
            "standwin": _fmt_standwin,
        }.get(key, _fmt_generic)
        body = formatter(rows)
        return f"{METRICS[key]} {start} → {end}\n{body}"

    async def _load_rows(self, key: str, start: str, end: str) -> list[dict[str, Any]]:
        """按 metric 决定走哪个入口：raw_window / daily_report / 现成 domain。

        几个类型是从别的 key 里拆出来的，映射写死在这里，省得两边各写一份。
        """
        raw_source = {
            "vitality": "vitality",
            "breath": "sleep",
            "stage": "sleep",
            "hrsample": "heart_rate",
            "standwin": "valid_stand",
        }
        if key == "score":
            return await backend.fetch_daily("sleep_raw", start, end, self._session_path)
        if key in raw_source:
            return await backend.fetch_raw(raw_source[key], start, end, self._session_path)
        if backend.has_daily(key):
            return await backend.fetch_daily(key, start, end, self._session_path)
        return await backend.fetch(key, start, end, self._session_path)

    def _render_chart(self, metric: str, rows: list[dict[str, Any]], start: str, end: str) -> Path | None:
        """画图，返回图片路径；画不出来就返回 None。"""
        import time

        if not charts.available():
            return None
        name = f"{metric}_{start}_{end}_{int(time.time() % 1000)}.png"
        out = backend.CHART_DIR / name
        try:
            if metric == "sleep":
                return charts.draw_sleep(rows, out, f"睡眠结构 {start} → {end}")
            if metric == "steps":
                # 步数柱上叠距离和消耗，一条图看完量级
                return charts.draw_bar(
                    rows, out, "steps", f"步数 {start} → {end}", "步",
                    extra=("distance_m", "距离", "km", 1000),
                )
            if metric == "heart":
                return charts.draw_heart(rows, out)
            if metric == "spo2":
                return charts.draw_bar(
                    rows, out, "avg_spo2_pct", f"血氧 {start} → {end}", "%", "#5fa86b"
                )
            if metric == "activity":
                return charts.draw_bar(
                    rows, out, "active_kcal", f"活动热量 {start} → {end}", "kcal", "#d69a4a"
                )
            if metric == "calories":
                return charts.draw_bar(
                    rows, out, "calories", f"活动卡路里 {start} → {end}", "kcal", "#d69a4a"
                )
            if metric == "intensity":
                return charts.draw_bar(
                    rows, out, "duration", f"中高强度分钟 {start} → {end}", "分", "#5fa86b",
                    target=30, target_name="日目标",
                )
            if metric == "stand":
                return charts.draw_bar(
                    rows, out, "count", f"有效站立 {start} → {end}", "次", "#8f6fd0"
                )
            if metric == "score":
                return charts.draw_bar(
                    rows, out, "sleep_score", f"睡眠评分 {start} → {end}", "", "#3f7fd0",
                    target=80, target_name="良好线",
                )
            if metric == "breath":
                return charts.draw_bar(
                    rows, out, "breath_quality", f"睡眠呼吸质量 {start} → {end}", "", "#5f8fa8"
                )
            if metric == "stage":
                return charts.draw_sleep_timeline(
                    rows, out, min(len(rows), 7), f"睡眠分期 {start} → {end}"
                )
            if metric == "standwin":
                return charts.draw_stand_windows(
                    rows, out, f"有效站立 {start} → {end}"
                )
            if metric == "hrsample":
                return charts.draw_hr_points(
                    rows, out, f"心率散点 {start} → {end}"
                )
            if metric == "vitality":
                # 堆叠看当日低/中/高强度贡献，红线是累计值
                return charts.draw_stacked(
                    rows, out,
                    [
                        ("daily_low_intensity_vitality", "低强度", "#9fc4e8"),
                        ("daily_medium_intensity_vitality", "中强度", "#4f8fd6"),
                        ("daily_high_intensity_vitality", "高强度", "#2b4a8b"),
                    ],
                    f"元气值 {start} → {end}",
                    line_key="latest_accumulated_vitality",
                    line_name="累计",
                )
        except Exception:
            logger.exception("[mifit] 画图失败")
        return None

    @llm_tool(name="mifit_chart")
    async def mifit_chart(
        self,
        event: AstrMessageEvent,
        metric: str = "sleep",
        days: int = 7,
        end_date: str = "",
    ) -> str:
        """把手环数据画成图发给用户。

        metric 支持 sleep / steps / heart / spo2 / activity，其余类型没有图。
        days 是回看天数，end_date 是结束日期 YYYY-MM-DD。
        """
        if not backend.has_session(self._session_path):
            return "还没登录小米账号，先用 mifit_login 扫码"

        key = self._normalize_metric(metric)
        if key not in METRICS:
            return f"没有 {metric} 这种数据，能查的是 " + " / ".join(METRICS)
        chartable = {
            "sleep", "steps", "heart", "spo2", "activity", "calories",
            "intensity", "stand", "score", "vitality", "breath",
            "stage", "hrsample", "standwin",
        }
        if key not in chartable:
            return (
                f"{METRICS[key]}没有图，能画的是 睡眠 / 步数 / 心率 / 血氧 / 活动 / "
                "卡路里 / 中高强度 / 站立 / 评分 / 元气 / 呼吸 / 分期 / 心率采样 / 站立时段"
            )

        days = max(1, min(int(days or self.default_days), self.max_rows))
        try:
            start, end = backend.date_range(days, end_date or None)
        except ValueError:
            return "日期格式不对，要 YYYY-MM-DD"

        try:
            rows = await self._load_rows(key, start, end)
        except Exception as exc:
            name = type(exc).__name__
            if name in {"AuthError", "RenewalError"}:
                return f"会话失效了（{name}），得重新用 mifit_login 扫码"
            return f"读数据失败：{name}: {exc}"

        if not rows:
            return f"{start} 到 {end} 的{METRICS[key]}没有记录，手环可能没同步到云"

        if not charts.available():
            return "服务器上没装 matplotlib，画不了图，只能给文字"

        chart = await asyncio.to_thread(self._render_chart, key, rows, start, end)
        if chart is None:
            return f"{METRICS[key]}的数据画不出图，可能字段不全，用 mifit_health 看文字版吧"

        try:
            await event.send(event.chain_result([Image.fromFileSystem(str(chart))]))
        except Exception as exc:
            logger.warning(f"[mifit] 图表发送失败: {exc}")
            return f"图画好了但没发出去：{chart}"
        return f"{METRICS[key]} {start} → {end} 的图已经发出去了"

    @filter.command("mifit_login")
    async def cmd_login(self, event: AstrMessageEvent):
        """发二维码登录小米账号。"""
        try:
            info = backend.start_login(self._session_path)
        except Exception as exc:
            yield event.plain_result(f"取登录二维码失败：{type(exc).__name__}: {exc}")
            return
        yield event.chain_result([Image.fromFileSystem(str(info["qr_path"]))])
        yield event.plain_result(
            f"用小米运动健康 App 的扫一扫直接扫；微信扫只会打开链接。令牌 {info['token']}"
        )

    @filter.command("mifit")
    async def cmd_query(self, event: AstrMessageEvent, metric: str = "sleep", days: str = "7"):
        """查手环数据，/mifit 睡眠 3"""
        text = await self.mifit_health(event, metric=metric, days=int(days or 7))
        yield event.plain_result(text)

    @filter.command("mifit_chart")
    async def cmd_chart(self, event: AstrMessageEvent, metric: str = "sleep", days: str = "7"):
        """画图，/mifit_chart 睡眠 7"""
        try:
            count = int(days or 7)
        except ValueError:
            count = 7
        text = await self.mifit_chart(event, metric=metric, days=count)
        yield event.plain_result(text)
