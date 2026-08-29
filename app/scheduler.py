import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from campus.client import create_client
from campus.task import is_attendance_task
from .campus_accounts import account_device, get_account, list_accounts
from .db import get_setting, log_event, now_iso, set_settings
from .db import connect
from .location import match_task_place, normalize_for_task, verify_location

_lock = threading.Lock()
_started = False
_next_run = None
_scheduled_tasks = {}
_scheduled_lock = threading.Lock()


def enabled() -> bool:
    return get_setting("auto_enabled", os.getenv("AUTO_ENABLED", "false")).lower() == "true"


def monitoring_window() -> bool:
    now = datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Shanghai"))).strftime("%H:%M")
    start = get_setting("monitor_start", os.getenv("MONITOR_START", "20:00"))
    end = get_setting("monitor_end", os.getenv("MONITOR_END", "23:30"))
    return start <= now <= end


def poll():
    set_settings({"last_check": now_iso()})
    if not enabled():
        return
    if not monitoring_window():
        return
    accounts = [account for account in list_accounts(include_cookie=True) if account["auto_enabled"]]
    for account in accounts:
        try:
            client = create_client(account["session_cookie"], account_device(account))
            tasks = client.list_today()
            log_event("POLL_OK", f"Account {account['name']}: {len(tasks)} task(s) returned")
            if os.getenv("CPDAILY_SUBMIT_ENABLED", "false").lower() != "true":
                continue
            for task in tasks:
                if task.completed or not is_attendance_task(task.name) or _already_attempted(account["id"], task.task_id):
                    continue
                _schedule_task(account, task)
        except Exception as exc:
            log_event("POLL_SKIPPED", f"Account {account['name']}: {exc}", "WARNING")


def _schedule_task(account, task):
    key = (account["id"], task.task_id)
    now = datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Shanghai")))
    task_start = _parse_time(task.start_time)
    detection_base = task_start if task_start and task_start > now else now
    run_at = detection_base + timedelta(seconds=60)
    delay = max(1, (run_at - now).total_seconds())
    with _scheduled_lock:
        if key in _scheduled_tasks:
            return
        timer = threading.Timer(delay, _run_scheduled_task, args=(account["id"], task.task_id))
        timer.daemon = True
        _scheduled_tasks[key] = {"timer": timer, "run_at": run_at}
        timer.start()
    log_event("TASK_SCHEDULED", f"Account {account['name']}: attendance scheduled for {run_at.isoformat()}")


def _run_scheduled_task(account_id, task_id):
    key = (account_id, task_id)
    try:
        account = get_account(account_id, include_cookie=True)
        if not account or not account["auto_enabled"] or not enabled():
            return
        if os.getenv("CPDAILY_SUBMIT_ENABLED", "false").lower() != "true" or _already_attempted(account_id, task_id):
            return
        client = create_client(account["session_cookie"], account_device(account))
        task = next((item for item in client.list_today() if item.task_id == task_id and not item.completed), None)
        if not task:
            log_event("SCHEDULED_TASK_GONE", f"Account {account['name']}: task is no longer pending")
            return
        _process_task(client, account, task)
    except Exception as exc:
        log_event("SCHEDULED_TASK_FAILED", f"Account {account_id}: {exc}", "WARNING")
    finally:
        with _scheduled_lock:
            _scheduled_tasks.pop(key, None)


def _process_task(client, account, task):
    detail = client.detail(task)
    if not _task_window_open(detail):
        log_event("TASK_NOT_OPEN", "Attendance task is not in its active time window")
        return
    location = _latest_location()
    if not location:
        log_event("LOCATION_REQUIRED", "No fresh trusted-device location is available", "WARNING")
        return
    valid, reason = verify_location(location["latitude"], location["longitude"], location["observed_at"], location["accuracy"] or 0)
    if not valid:
        log_event("LOCATION_REJECTED", reason, "WARNING")
        return
    latitude, longitude = normalize_for_task(location["latitude"], location["longitude"], location["coordinate_system"])
    place = match_task_place(latitude, longitude, detail.get("signPlaceSelected"))
    if not place:
        log_event("OUTSIDE_TASK_GEOFENCE", "Fresh location is outside this task's allowed places", "WARNING")
        return
    _save_task(task, detail)
    result = client.submit(task, {
        "verified": True,
        "latitude": latitude,
        "longitude": longitude,
        "address": str(place.get("address") or location["address"] or ""),
        "is_malposition": False,
    })
    confirmed = all(item.task_id != task.task_id for item in client.list_today() if not item.completed)
    status = "SUCCESS" if confirmed else "SUBMITTED_UNCONFIRMED"
    _save_checkin(account, task, status, "Submission confirmed" if confirmed else "Submission sent; confirmation pending")
    log_event("CHECKIN_SUCCESS" if confirmed else "CHECKIN_UNCONFIRMED", status, "INFO" if confirmed else "WARNING")
    if confirmed:
        set_settings({"last_success": now_iso()})
    return result


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(os.getenv("TZ", "Asia/Shanghai")))
        return parsed
    except ValueError:
        return None


def _task_window_open(detail):
    current = _parse_time(detail.get("currentTime")) or datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Shanghai")))
    start = _parse_time(detail.get("singleTaskBeginTime"))
    end = _parse_time(detail.get("singleTaskEndTime"))
    return bool(start and end and start <= current <= end)


def _latest_location():
    with connect() as db:
        return db.execute(
            "SELECT latitude,longitude,accuracy,observed_at,address,coordinate_system FROM locations WHERE verified=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()


def _already_attempted(account_id, task_id):
    with connect() as db:
        row = db.execute(
            "SELECT 1 FROM checkins WHERE account_id=? AND task_id=? AND status IN ('SUCCESS','SUBMITTED_UNCONFIRMED') LIMIT 1",
            (account_id, task_id),
        ).fetchone()
    return bool(row)


def _save_task(task, detail):
    import json
    at = now_iso()
    safe_detail = {key: detail.get(key) for key in ("signMode", "signCondition", "singleTaskBeginTime", "singleTaskEndTime", "isPhoto")}
    with connect() as db:
        db.execute(
            "INSERT INTO tasks(task_id,task_name,start_time,end_time,status,detail_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id) DO UPDATE SET task_name=excluded.task_name,start_time=excluded.start_time,end_time=excluded.end_time,status=excluded.status,detail_json=excluded.detail_json,updated_at=excluded.updated_at",
            (task.task_id, task.name, task.start_time, task.end_time, "PENDING", json.dumps(safe_detail), at, at),
        )


def _save_checkin(account, task, status, message):
    at = now_iso()
    with connect() as db:
        db.execute(
            "INSERT INTO checkins(date,task_id,task_name,start_time,end_time,submit_time,status,response_message,created_at,updated_at,account_id,account_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (at[:10], task.task_id, task.name, task.start_time, task.end_time, at, status, message, at, at, account["id"], account["name"]),
        )


def start_scheduler():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, name="campustoday-scheduler", daemon=True).start()


def _loop():
    global _next_run
    interval = max(1, int(os.getenv("QUERY_INTERVAL_MINUTES", "5"))) * 60
    while True:
        _next_run = datetime.now(timezone.utc) + timedelta(seconds=interval)
        threading.Event().wait(interval)
        try:
            poll()
        except Exception as exc:
            log_event("SCHEDULER_ERROR", str(exc), "ERROR")


def status():
    return {
        "running": _started,
        "enabled": enabled(),
        "last_check": get_setting("last_check"),
        "last_success": get_setting("last_success"),
        "next_run": _next_run.isoformat() if _next_run else None,
        "integration": os.getenv("CPDAILY_MODE", "disabled"),
    }
