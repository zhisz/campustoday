import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .campus_accounts import list_accounts
from .db import connect
from .task_store import list_account_tasks, task_sync_state


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
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
        automatic_submits = {}
        if selected:
            automatic_successes = db.execute(
                "SELECT COUNT(*) AS count FROM checkins WHERE account_id=? AND status='SUCCESS'", (selected["id"],)
            ).fetchone()["count"]
            automatic_submits = {
                row["task_id"]: row["submit_time"] for row in db.execute(
                    "SELECT task_id,submit_time FROM checkins WHERE account_id=? AND status='SUCCESS' AND task_id IS NOT NULL",
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
        "cloud_updated_at": None,
    }

    for account in account_rows:
        result["accounts"].append(_account_view(account))
    if selected:
        task_records = list_account_tasks(selected["id"])
        pending = [record for record in task_records if _task_record_pending(record)]
        pending.sort(key=lambda record: record.get("start_time") or record.get("last_seen_at") or "")
        result["upcoming"] = [_cached_task_view(record) for record in pending]
        result["school_history"] = [
            _cached_history_view(record, automatic_submits.get(record["task_id"]))
            for record in task_records
            if _task_record_in_history(record)
        ]
        result["school_signed_count"] = sum(
            1 for record in task_records
            if _task_record_signed(record) and _record_date(record).startswith(result["history_month"])
        )
        sync_state = task_sync_state(selected["id"])
        result["cloud_updated_at"] = _format_datetime(sync_state["synced_at"], "尚未同步")
        result["school_error"] = sync_state["last_error"]
        selected_status = selected["session_status"]
        result["selected_account"] = _account_view(selected, selected_status)
        for account in result["accounts"]:
            account["selected"] = account["id"] == selected["id"]
            if account["selected"]:
                account["session_status"] = selected_status
                account["session_valid"] = selected_status == "VALID"
    return result


def invalidate_school_cache(account_id):
    # Kept as a compatibility hook for account-update routes. Dashboard and
    # mobile reads are database-only and have no process-local school cache.
    return None


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


def _task_record_pending(record):
    if bool(record.get("completed")):
        return False
    now = datetime.now(LOCAL_TZ)
    end = _parse_datetime(record.get("end_time"))
    if end:
        return end >= now
    record_date = _record_date(record)
    if record_date:
        return record_date >= now.strftime("%Y-%m-%d")
    last_seen = _parse_datetime(record.get("last_seen_at"))
    return bool(last_seen and last_seen.date() == now.date())


def _task_record_in_history(record):
    if bool(record.get("completed")):
        return True
    end = _parse_datetime(record.get("end_time"))
    return bool(end and end < datetime.now(LOCAL_TZ))


def _task_record_signed(record):
    if not bool(record.get("completed")):
        return False
    group = str(record.get("source_group") or record.get("school_status") or "")
    return group in {"signedTasks", "codeRcvdTasks", "localAutomatic", "SUCCESS"}


def _cached_task_view(record):
    now = datetime.now(LOCAL_TZ)
    start = _parse_datetime(record.get("start_time"))
    end = _parse_datetime(record.get("end_time"))
    if start and now < start:
        state, tone = "未开始", "neutral"
    elif end and now > end:
        state, tone = "已截止", "warn"
    else:
        state, tone = "可签到", "ok"
    return {
        "id": record.get("task_id"),
        "name": record.get("task_name") or "未命名任务",
        "start": _format_datetime(record.get("start_time")),
        "end": _format_datetime(record.get("end_time")),
        "state": state,
        "tone": tone,
    }


def _cached_history_view(record, automatic_submit=None):
    group = str(record.get("source_group") or record.get("school_status") or "")
    completed = bool(record.get("completed"))
    if group == "codeRcvdTasks":
        status, tone = "已扫码", "ok"
    elif group == "registerLeaveTasks":
        status, tone = "登记离校", "neutral"
    elif group == "leaveTasks":
        status, tone = "已请假", "neutral"
    elif completed:
        status, tone = "已签到", "ok"
    else:
        status, tone = "未签到", "warn"
    signed_value = record.get("signed_time") or automatic_submit
    signed = _parse_datetime(signed_value)
    start = _parse_datetime(record.get("start_time"))
    end = _parse_datetime(record.get("end_time"))
    if signed:
        display_time = signed.strftime("%H:%M:%S")
    elif start and end:
        display_time = f"{start:%H:%M}–{end:%H:%M}"
    else:
        display_time = "—"
    return {
        "id": record.get("task_id"),
        "date": _record_date(record) or "—",
        "name": record.get("task_name") or "未命名任务",
        "status": status,
        "tone": tone,
        "time": display_time,
        "publisher": record.get("publisher") or "—",
        "automatic": automatic_submit is not None,
    }


def _record_date(record):
    explicit = str(record.get("record_date") or "").strip()
    if explicit:
        return explicit[:10]
    for key in ("signed_time", "end_time", "start_time", "completed_at", "last_seen_at"):
        parsed = _parse_datetime(record.get(key))
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    return ""


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


def _account_view(account_row, session_status=None):
    base_url = os.getenv("CPDAILY_BASE_URL", "https://fdm.jxust.edu.cn")
    cookie = account_row.get("session_cookie", "")
    auth_type = cookie.split("=", 1)[0].strip() if "=" in cookie else "未配置"
    session_status = session_status or account_row.get("session_status") or "UNKNOWN"
    account = {
        "id": account_row["id"],
        "label": account_row["name"],
        "auto_enabled": bool(account_row["auto_enabled"]),
        "school": "江西理工大学",
        "session_status": session_status,
        "session_valid": session_status == "VALID",
        "host": urlsplit(base_url).hostname or base_url,
        "auth_type": auth_type,
        "name": "学生端签到接口未提供",
        "student_id": "学生端签到接口未提供",
        "device": f'{account_row.get("device_model") or "未配置"} / {account_row.get("system_name") or "—"} {account_row.get("system_version") or ""}'.strip(),
        "app_version": account_row.get("app_version") or "未配置",
    }
    return account
