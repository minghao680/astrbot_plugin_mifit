"""mifit 调用层的薄封装。

几点是踩过的坑：
- 容器里默认时区可能是 UTC，直接用 "YYYY-MM-DD" 会按 UTC 切天，跨零点的睡觉
  会被算到前一天。所以这里统一把 TZ 环境变量设成配置里的时区。
- 登录是阻塞的：QR 长轮询会一直挂着等手机确认，必须丢到线程里跑，不能卡事件循环。
- mifit 的 mcp 依赖和 AstrBot 自带的 mcp 版本冲突，所以不要 import mifit.mcp_server。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

def _plugin_data_dir() -> Path:
    """插件数据目录：优先用 AstrBot 自己的路径工具，拿不到再退回 cwd/data。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path())
    except Exception:
        base = Path.cwd() / "data" / "plugin_data"
    return base / "astrbot_plugin_mifit"


APP_DIR = _plugin_data_dir()
QR_DIR = APP_DIR / "qr"
CHART_DIR = APP_DIR / "charts"

_PENDING: dict[str, dict[str, Any]] = {}


def ensure_dirs() -> None:
    for path in (APP_DIR, QR_DIR, CHART_DIR):
        path.mkdir(parents=True, exist_ok=True)


def set_timezone(tz: str) -> None:
    if tz:
        os.environ["TZ"] = tz
        try:
            time.tzset()
        except AttributeError:
            pass


def session_path(custom: str) -> Path:
    """会话文件位置：配置 > 环境变量 MIFIT_SESSION > 插件数据目录。"""
    if custom and custom.strip():
        return Path(custom.strip()).expanduser()
    env = os.environ.get("MIFIT_SESSION", "").strip()
    if env:
        return Path(env).expanduser()
    return APP_DIR / "session.json"


def has_session(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _client(path: Path) -> Any:
    from mifit import MiFit
    from mifit.auth.session import load_session

    return MiFit(session=load_session(path))


def _result_rows(result: Any) -> list[Any]:
    """mifit 的 DomainResult 里真正的记录字段名不是固定的，挨个试。"""
    for attr in ("records", "items", "data", "entries", "summary"):
        value = getattr(result, attr, None)
        if isinstance(value, list):
            return value
    if isinstance(result, list):
        return result
    return []


def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for method in ("to_dict", "as_dict", "model_dump"):
        fn = getattr(obj, method, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                continue
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"value": obj}


def _plain(value: Any) -> Any:
    """把 dataclass / 枚举 / datetime 压成能 json 的东西。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    name = getattr(value, "name", None)
    if isinstance(name, str) and hasattr(value, "value"):
        return name
    return _plain(_to_dict(value))


# metric 名字和 mifit 客户端的方法名不是一回事，这里显式转一层。
_METHOD = {
    "sleep": "sleep",
    "steps": "steps",
    "heart": "heart_rate_daily",
    "spo2": "spo2_daily",
    "stress": "stress_daily",
    "activity": "activity_daily",
    "consumption": "consumption",
}


def _fetch_sync(path: Path, metric: str, start: str, end: str) -> list[dict[str, Any]]:
    client = _client(path)
    method = _METHOD.get(metric, metric)
    fn = getattr(client, method)
    result = fn(start, end)
    rows = [_plain(_to_dict(row)) for row in _result_rows(result)]
    # 统一按日期正序返回，画图和文字都省得再各自翻一遍
    rows.sort(key=lambda r: str(r.get("date") or ""))
    return rows


async def fetch(metric: str, start: str, end: str, path: Path) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_sync, path, metric, start, end)


# 底下这些 key 不在 mifit 的 domain 里，得走 daily_report / daily_fitness 原样读。
_DAILY_KEYS = {
    "calories": ("calories", "daily_report"),
    "stand": ("valid_stand", "daily_report"),
    "intensity": ("intensity", "daily_report"),
    "goal": ("goal", "daily_fitness"),
    "heart_rate": ("heart_rate", "daily_report"),
    "sleep_raw": ("sleep", "daily_report"),
}


def _epoch_to_day(epoch: Any) -> str:
    """daily_report 的 time 是本地日期零点的 UTC 秒，按本地时区还原成日期。"""
    try:
        return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _fetch_daily_sync(path: Path, metric: str, start: str, end: str) -> list[dict[str, Any]]:
    key, tag = _DAILY_KEYS[metric]
    client = _client(path)
    result = client.daily_report(key, start, end, tag=tag)
    rows: list[dict[str, Any]] = []
    for row in getattr(result, "raw", []) or []:
        row = _plain(row)
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if not isinstance(value, dict):
            value = {"value": value}
        item = {"date": _epoch_to_day(row.get("time"))}
        item.update(value)
        rows.append(item)
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


async def fetch_daily(metric: str, start: str, end: str, path: Path) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_daily_sync, path, metric, start, end)


def _fetch_raw_sync(path: Path, key: str, start: str, end: str) -> list[dict[str, Any]]:
    client = _client(path)
    result = client.raw_window(key, start, end)
    rows: list[dict[str, Any]] = []
    for row in getattr(result, "raw", []) or []:
        row = _plain(row)
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if isinstance(value, str):
            try:
                import json

                value = json.loads(value)
            except Exception:
                value = {"value": value}
        if not isinstance(value, dict):
            value = {"value": value}
        item = {"date": _epoch_to_day(row.get("time"))}
        item.update(value)
        rows.append(item)
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


async def fetch_raw(key: str, start: str, end: str, path: Path) -> list[dict[str, Any]]:
    """raw_window 能读到 daily_report 拿不到的 key，vitality 就在这儿。"""
    return await asyncio.to_thread(_fetch_raw_sync, path, key, start, end)


def has_daily(metric: str) -> bool:
    return metric in _DAILY_KEYS


def date_range(days: int, end: str | None = None) -> tuple[str, str]:
    days = max(1, min(int(days), 366))
    if end:
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    else:
        end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days - 1)
    return start_date.isoformat(), end_date.isoformat()


def make_qr(payload: str, name: str) -> Path:
    import qrcode

    ensure_dirs()
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    out = QR_DIR / f"{name}.png"
    img.save(out)
    return out


def start_login(path: Path) -> dict[str, Any]:
    """开一个登录会话，返回二维码；真正的等待放到后台线程里。"""
    from mifit.auth.qr import request_qr_login

    ensure_dirs()
    challenge = request_qr_login()
    token = f"login_{int(time.time())}"
    out = make_qr(challenge.qr_payload, token)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    task = loop.create_task(_finish_login(challenge, path, token))
    _PENDING[token] = {"task": task, "state": "waiting", "error": ""}
    return {
        "token": token,
        "qr_path": out,
        "login_url": challenge.login_url,
    }


def _login_sync(challenge: Any, path: Path) -> None:
    from mifit.auth.qr import exchange_sts, poll_qr_login
    from mifit.auth.session import save_session

    result = poll_qr_login(challenge)
    session = exchange_sts(result, "miothealth")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_session(session, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


async def _finish_login(challenge: Any, path: Path, token: str) -> None:
    entry = _PENDING.get(token)
    if entry is None:
        return
    try:
        await asyncio.to_thread(_login_sync, challenge, path)
        entry["state"] = "ok"
    except Exception as exc:  # 登录失败不该把插件带崩
        entry["state"] = "failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"


def login_state(token: str) -> tuple[str, str]:
    entry = _PENDING.get(token)
    if entry is None:
        return "missing", ""
    return entry["state"], entry["error"]


def drop_login(token: str) -> None:
    entry = _PENDING.pop(token, None)
    if entry:
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
