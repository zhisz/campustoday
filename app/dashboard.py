import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from campus.client import create_client
from campus.device import configured_device

from .db import connect


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
HISTORY_GROUPS = (
    ("signedTasks", "已签到", "ok"),
    ("codeRcvdTasks", "已扫码", "ok"),
    ("registerLeaveTasks", "登记离校", "neutral"),
    ("leaveTasks", "已请假", "neutral"),
    ("unSignedTasks", "未签到", "warn"),
)


def build_dashboard():
    with connect() as db:
        location_row = db.execute(
            "SELECT latitude,longitude,accuracy,observed_at,received_at,verified,reason,address,coordinate_system "
            "FROM locations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        automatic_successes = db.execute("SELECT COUNT(*) AS count FROM checkins WHERE status='SUCCESS'").fetchone()["count"]
        automatic_records = [
            dict(row) for row in db.execute(
                "SELECT date,task_name,status,submit_time,response_message FROM checkins ORDER BY id DESC LIMIT 8"
            ).fetchall()
        ]

    location = _location_view(dict(location_row)) if location_row else None
    result = {
        "location": location,
        "automatic_successes": automatic_successes,
        "automatic_records": automatic_records,
        "upcoming": [],
        "school_history": [],
        "school_signed_count": 0,
        "school_error": None,
        "account": _account_view(False),
        "history_month": datetime.now(LOCAL_TZ).strftime("%Y-%m"),
    }

    try:
        client = create_client()
        tasks = client.list_today()
        result["upcoming"] = [_task_view(task) for task in tasks if not task.completed]
        history = client.month_history(result["history_month"])
        result["school_history"], result["school_signed_count"] = _flatten_history(history.get("rows", []), result["history_month"])
        result["account"] = _account_view(True)
    except Exception as exc:
        result["school_error"] = str(exc)
    return result


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _format_datetime(value, fallback="—"):
    parsed = _parse_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else (str(value) if value else fallback)


def _location_view(row):
    observed = _parse_datetime(row.get("observed_at"))
    age_seconds = int((datetime.now(LOCAL_TZ) - observed).total_seconds()) if observed else None
    max_age = int(os.getenv("LOCATION_MAX_AGE_SECONDS", "300"))
    return {
        **row,
        "latitude": f'{float(row["latitude"]):.6f}',
        "longitude": f'{float(row["longitude"]):.6f}',
        "accuracy": f'{float(row.get("accuracy") or 0):.1f}',
        "observed_display": _format_datetime(row.get("observed_at")),
        "received_display": _format_datetime(row.get("received_at")),
        "age_seconds": max(age_seconds, 0) if age_seconds is not None else None,
        "fresh": bool(row.get("verified")) and age_seconds is not None and 0 <= age_seconds <= max_age,
    }


def _task_view(task):
    now = datetime.now(LOCAL_TZ)
    start, end = _parse_datetime(task.start_time), _parse_datetime(task.end_time)
    if start and now < start:
        state, tone = "未开始", "neutral"
    elif end and now > end:
        state, tone = "已截止", "warn"
    else:
        state, tone = "可签到", "ok"
    return {
        "name": task.name or "未命名任务",
        "start": _format_datetime(task.start_time),
        "end": _format_datetime(task.end_time),
        "state": state,
        "tone": tone,
    }


def _flatten_history(rows, year_month):
    records, signed_count = [], 0
    for day in rows:
        if not isinstance(day, dict):
            continue
        day_number = str(day.get("dayInMonth") or "").zfill(2)
        date = f"{year_month}-{day_number}" if day_number.strip("0") else year_month
        for group, label, tone in HISTORY_GROUPS:
            items = day.get(group) or []
            if group in {"signedTasks", "codeRcvdTasks"}:
                signed_count += len(items) if isinstance(items, list) else 0
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                records.append({
                    "date": date,
                    "name": str(item.get("taskName") or "未命名任务"),
                    "status": label,
                    "tone": tone,
                    "time": _history_time(item),
                    "publisher": str(item.get("senderUserName") or "—"),
                })
    records.sort(key=lambda item: (item["date"], item["time"]), reverse=True)
    return records[:30], signed_count


def _history_time(item):
    signed = item.get("rateSignDate") or item.get("currentTime")
    if signed:
        parsed = _parse_datetime(signed)
        return parsed.strftime("%H:%M:%S") if parsed else str(signed)
    start = item.get("singleTaskBeginTime") or item.get("rateTaskBeginTime")
    end = item.get("singleTaskEndTime") or item.get("rateTaskEndTime")
    start_dt, end_dt = _parse_datetime(start), _parse_datetime(end)
    if start_dt and end_dt:
        return f"{start_dt:%H:%M}–{end_dt:%H:%M}"
    return "—"


def _account_view(session_valid):
    base_url = os.getenv("CPDAILY_BASE_URL", "https://fdm.jxust.edu.cn")
    cookie = os.getenv("CPDAILY_SESSION_COOKIE", "")
    auth_type = cookie.split("=", 1)[0].strip() if "=" in cookie else "未配置"
    account = {
        "school": "江西理工大学",
        "session_valid": session_valid,
        "host": urlsplit(base_url).hostname or base_url,
        "auth_type": auth_type,
        "name": "学生端签到接口未提供",
        "student_id": "学生端签到接口未提供",
        "device": "未配置",
        "app_version": "未配置",
    }
    try:
        device = configured_device()
        account["device"] = f"{device.model} / {device.system_name} {device.system_version}"
        account["app_version"] = device.app_version
    except RuntimeError:
        pass
    return account
