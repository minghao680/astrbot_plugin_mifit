"""扫小米健康接口的 key 空间，看看哪些 key 有数据。

用法：
    export MIFIT_SESSION=~/.mifit/session.json
    python scan_keys.py            # 扫内置候选表
    python scan_keys.py sleep foo  # 只扫指定的几个 key

两个入口都试：
    daily_report  get_aggregated_fitness_data_by_time  按天聚合的摘要
    raw_window    get_fitness_data_by_time             原始点数据
vitality 这类只在 raw 里活着，所以两边都得摸。

注意：这个脚本会真的请求小米服务器，别拿去高频刷。
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mifit import MiFit
from mifit.auth.session import load_session
from mifit.paths import default_session_path


def session_file() -> Path:
    """会话路径：优先 MIFIT_SESSION，其次 mifit 自己的默认位置。"""
    custom = os.environ.get("MIFIT_SESSION", "").strip()
    return Path(custom).expanduser() if custom else default_session_path()


CANDIDATES = """
vitality vitality_score vital_energy energy stamina vigor body_battery readiness
pai training_load fitness_index fitness_age body_age vo2max vo2_max max_oxygen
oxygen spo2 blood_oxygen spo2_altitude altitude pressure air_pressure stress
stress_score relax breath breathing respiratory_rate respiratory sleep_breath
sleep_breathing snore snoring apnea atrial_fibrillation afib abnormal_heart_beat
heart_rhythm ecg heart_rate resting_heart_rate rhr hrv hrv_daily
heart_rate_variability temperature skin_temperature body_temperature
wrist_temperature weight body_weight body_fat body_composition bmi muscle bone
water body_water protein metabolism bmr basal_metabolism blood_pressure
blood_glucose glucose uric_acid menstrual period cycle ovulation sport workout
workouts exercise activity activity_minutes activity_count steps step distance
calories calorie consumption total_calories active_calories goal daily_goal stand
valid_stand stand_count sit sedentary intensity intensity_minutes sleep
sleep_score nap sleep_stage sleep_detail meditation mindfulness mood emotion
score health_score wellness
""".split()


def _unwrap(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _fields(value) -> str:
    value = _unwrap(value)
    if isinstance(value, dict):
        return ", ".join(sorted(value.keys())[:12])
    return type(value).__name__


def probe(key: str, session: Path, start: str, end: str) -> list[tuple[str, str]]:
    hits = []
    client = MiFit(session=load_session(session))
    try:
        rows = getattr(client.raw_window(key, start, end), "raw", []) or []
        if rows:
            hits.append(("raw", f"{len(rows)} 行, 字段: {_fields(rows[0].get('value'))}"))
    except Exception:
        pass
    try:
        rows = getattr(client.daily_report(key, start, end), "raw", []) or []
        if rows:
            hits.append(("daily", f"{len(rows)} 行, 字段: {_fields(rows[0].get('value'))}"))
    except Exception:
        pass
    return hits


def main(keys: list[str], start: str, end: str) -> None:
    session = session_file()
    if not session.exists():
        print(f"找不到会话文件：{session}")
        print("先扫码登录（插件里的 mifit_login，或 mifit login 命令）再跑这个脚本。")
        raise SystemExit(1)

    found = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(probe, k, session, start, end): k for k in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                hits = future.result()
            except Exception as exc:
                print(f"! {key} 探测失败 {type(exc).__name__}", flush=True)
                continue
            for entry, detail in hits:
                found += 1
                print(f"HIT {key:22s} [{entry:5s}] {detail}", flush=True)
    print(f"\n扫了 {len(keys)} 个 key，命中 {found} 条", flush=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    window = os.environ.get("MIFIT_WINDOW", "").strip()
    if window and "," in window:
        start, end = [s.strip() for s in window.split(",", 1)]
    else:
        from datetime import date, timedelta

        today = date.today()
        start = (today - timedelta(days=6)).isoformat()
        end = today.isoformat()
    main(args or CANDIDATES, start, end)
