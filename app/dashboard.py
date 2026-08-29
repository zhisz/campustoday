import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from campus.client import create_client
from campus.device import configured_device

from .campus_accounts import list_accounts
from .db import connect


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
SCHOOL_CACHE_SECONDS = 180
_school_cache = {}
_school_cache_lock = threading.Lock()
HISTORY_GROUPS = (
    ("signedTasks", "已签到", "ok"),
    ("codeRcvdTasks", "已扫码", "ok"),
    ("registerLeaveTasks", "登记离校", "neutral"),
    ("leaveTasks", "已请假", "neutral"),
    ("unSignedTasks", "未签到", "warn"),
)


def build_dashboard(account_id=None):
    account_rows = list_accounts(include_cookie=True)
    selected = next((account for account in account_rows if account["id"] == account_id), None)
    selected = selected or (account_rows[0] if account_rows else None)
    with connect() as db:
        location_row = db.execute(
            "SELECT latitude,longitude,accuracy,observed_at,received_at,verified,reason,address,coordinate_system "
            "FROM locations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        automatic_successes = 0
        automatic_task_ids = set()
        if selected:
            automatic_successes = db.execute(
                "SELECT COUNT(*) AS count FROM checkins WHERE account_id=? AND status='SUCCESS'", (selected["id"],)
            ).fetchone()["count"]
            automatic_task_ids = {
                row["task_id"] for row in db.execute(
                    "SELECT task_id FROM checkins WHERE account_id=? AND status='SUCCESS' AND task_id IS NOT NULL",
                    (selected["id"],),
                ).fetchall()
            }

    location = _location_view(dict(location_row)) if location_row else None
    result = {
        "location": location,
        "automatic_successes": automatic_successes,
        "upcoming": [],
        "school_history": [],
        "school_signed_count": 0,
        "school_error": None,
        "accounts": [],
        "selected_account": None,
        "history_month": datetime.now(LOCAL_TZ).strftime("%Y-%m"),
    }

    for account in account_rows:
        result["accounts"].append(_account_view(account, account["session_status"] == "VALID"))
    if selected:
        session_valid = False
        try:
            school_data = _school_data(selected, result["history_month"])
            tasks = school_data["tasks"]
            result["upcoming"] = [_task_view(task) for task in tasks if not task.completed]
            history = school_data["history"]
            records, count = _flatten_history(history.get("rows", []), result["history_month"], automatic_task_ids)
            result["school_history"] = records
            result["school_signed_count"] = count
            session_valid = True
        except Exception as exc:
            result["school_error"] = str(exc)
        result["selected_account"] = _account_view(selected, session_valid)
        for account in result["accounts"]:
            account["selected"] = account["id"] == selected["id"]
            if account["selected"]:
                account["session_valid"] = session_valid
    return result


def invalidate_school_cache(account_id):
    with _school_cache_lock:
        _school_cache.pop(account_id, None)


def _school_data(account, year_month):
    key = account["id"]
    with _school_cache_lock:
        cached = _school_cache.get(key)
        if cached and time.monotonic() - cached["stored_at"] < SCHOOL_CACHE_SECONDS:
            if cached.get("error"):
                raise RuntimeError(cached["error"])
            return cached["data"]
        try:
            client = create_client(account["session_cookie"])
            data = {"tasks": client.list_today(), "history": client.month_history(year_month)}
            _school_cache[key] = {"stored_at": time.monotonic(), "data": data, "error": None}
            return data
        except Exception as exc:
            error = str(exc)
            _school_cache[key] = {"stored_at": time.monotonic(), "data": None, "error": error}
            raise


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


def _flatten_history(rows, year_month, automatic_task_ids):
    records, signed_count = [], 0
    for day in rows:
        if not isinstance(day, dict):
            continue
        raw_day = str(day.get("dayInMonth") or "").strip()
        if raw_day.startswith(f"{year_month}-"):
            date = raw_day
        else:
            day_number = raw_day.zfill(2)
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
                    "automatic": str(item.get("signInstanceWid") or "") in automatic_task_ids,
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


def _account_view(account_row, session_valid):
    base_url = os.getenv("CPDAILY_BASE_URL", "https://fdm.jxust.edu.cn")
    cookie = account_row.get("session_cookie", "")
    auth_type = cookie.split("=", 1)[0].strip() if "=" in cookie else "未配置"
    account = {
        "id": account_row["id"],
        "label": account_row["name"],
        "auto_enabled": bool(account_row["auto_enabled"]),
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
