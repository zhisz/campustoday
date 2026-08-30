import os
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from campus.client import create_client
from campus.jxust import UpstreamUnavailable
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
_submission_lock = threading.Lock()
_upstream_lock = threading.Lock()
_upstream_retry_after = 0
UPSTREAM_RETRY_SECONDS = 60


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
    if not _upstream_ready():
        log_event("POLL_DEFERRED", "School API is in temporary backoff", "WARNING")
        return
    accounts = _eligible_accounts()
    for account in accounts:
        try:
            client = create_client(account["session_cookie"], account_device(account), purpose="scheduler")
            tasks = client.list_today()
            _clear_upstream_backoff()
            log_event("POLL_OK", f"Account {account['name']}: {len(tasks)} task(s) returned")
            if os.getenv("CPDAILY_SUBMIT_ENABLED", "false").lower() != "true":
                continue
            for task in tasks:
                attempt_status = _attempt_status(account["id"], task.task_id)
                if attempt_status in {"SUBMITTED_UNCONFIRMED", "SUBMIT_UNKNOWN", "ATTEMPT_STARTED"}:
                    if task.completed:
                        _mark_checkin_success(account["id"], task.task_id, "Submission confirmed on a later poll")
                    continue
                if task.completed or not is_attendance_task(task.name) or attempt_status:
                    continue
                _schedule_task(account, task)
        except Exception as exc:
            log_event("POLL_SKIPPED", f"Account {account['name']}: {exc}", "WARNING")
            if _temporary_upstream_failure(exc):
                _defer_upstream()
                break


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
    retry = False
    try:
        with _submission_lock:
            if not _upstream_ready():
                raise RuntimeError("School API is in temporary backoff")
            account = get_account(account_id, include_cookie=True)
            if not account or not account["auto_enabled"] or not enabled() or not monitoring_window() or not _identity_can_submit(account):
                return
            if os.getenv("CPDAILY_SUBMIT_ENABLED", "false").lower() != "true" or _already_attempted(account_id, task_id):
                return
            client = create_client(account["session_cookie"], account_device(account), purpose="submission")
            task = next((item for item in client.list_today() if item.task_id == task_id and not item.completed), None)
            if not task:
                log_event("SCHEDULED_TASK_GONE", f"Account {account['name']}: task is no longer pending")
                return
            _clear_upstream_backoff()
            _process_task(client, account, task)
    except Exception as exc:
        log_event("SCHEDULED_TASK_FAILED", f"Account {account_id}: {exc}", "WARNING")
        retry = _temporary_upstream_failure(exc)
        if retry:
            _defer_upstream()
    finally:
        with _scheduled_lock:
            _scheduled_tasks.pop(key, None)
    if retry and enabled() and monitoring_window():
        _schedule_retry(account_id, task_id)


def _schedule_retry(account_id, task_id):
    key = (account_id, task_id)
    run_at = datetime.now(ZoneInfo(os.getenv("TZ", "Asia/Shanghai"))) + timedelta(seconds=UPSTREAM_RETRY_SECONDS)
    with _scheduled_lock:
        if key in _scheduled_tasks:
            return
        timer = threading.Timer(UPSTREAM_RETRY_SECONDS, _run_scheduled_task, args=(account_id, task_id))
        timer.daemon = True
        _scheduled_tasks[key] = {"timer": timer, "run_at": run_at}
        timer.start()
    log_event("TASK_RETRY_SCHEDULED", f"Account {account_id}: retry scheduled for {run_at.isoformat()}", "WARNING")


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
    if not _save_checkin(account, task, "ATTEMPT_STARTED", "Submission attempt started"):
        log_event("CHECKIN_DUPLICATE_PREVENTED", f"Account {account['id']}: task already attempted", "WARNING")
        return
    try:
        result = client.submit(task, {
            "verified": True,
            "latitude": latitude,
            "longitude": longitude,
            "address": str(place.get("address") or location["address"] or ""),
            "is_malposition": False,
        })
    except UpstreamUnavailable:
        # The shared gate raises this before any HTTP request is sent.  Remove
        # only this provisional row so the scheduler can safely retry later.
        _delete_unsent_checkin_attempt(account["id"], task.task_id)
        raise
    except Exception as exc:
        # Once a request may have left the process, never submit the same task
        # again automatically.  A later read-only poll can still confirm it.
        _mark_checkin_unknown(account["id"], task.task_id, exc)
        raise
    _mark_checkin_submitted(account["id"], task.task_id)
    log_event("CHECKIN_UNCONFIRMED", "SUBMITTED_UNCONFIRMED", "WARNING")
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
    return bool(_attempt_status(account_id, task_id))


def _attempt_status(account_id, task_id):
    with connect() as db:
        row = db.execute(
            "SELECT status FROM checkins WHERE account_id=? AND task_id=? AND status IN ('SUCCESS','SUBMITTED_UNCONFIRMED','SUBMIT_UNKNOWN','ATTEMPT_STARTED') ORDER BY id DESC LIMIT 1",
            (account_id, task_id),
        ).fetchone()
    return row["status"] if row else None


def _mark_checkin_success(account_id, task_id, message):
    at = now_iso()
    with connect() as db:
        db.execute(
            "UPDATE checkins SET status='SUCCESS',response_message=?,updated_at=? "
            "WHERE account_id=? AND task_id=? AND status IN ('SUBMITTED_UNCONFIRMED','SUBMIT_UNKNOWN','ATTEMPT_STARTED')",
            (message, at, account_id, task_id),
        )
    set_settings({"last_success": at})


def _mark_checkin_submitted(account_id, task_id):
    at = now_iso()
    with connect() as db:
        db.execute(
            "UPDATE checkins SET status='SUBMITTED_UNCONFIRMED',response_message=?,updated_at=? "
            "WHERE account_id=? AND task_id=? AND status='ATTEMPT_STARTED'",
            ("Submission accepted; confirmation pending", at, account_id, task_id),
        )


def _delete_unsent_checkin_attempt(account_id, task_id):
    with connect() as db:
        db.execute(
            "DELETE FROM checkins WHERE account_id=? AND task_id=? AND status='ATTEMPT_STARTED'",
            (account_id, task_id),
        )


def _mark_checkin_unknown(account_id, task_id, exc):
    at = now_iso()
    message = f"Submission result unknown; confirmation required: {exc.__class__.__name__}"
    with connect() as db:
        db.execute(
            "UPDATE checkins SET status='SUBMIT_UNKNOWN',response_message=?,updated_at=? "
            "WHERE account_id=? AND task_id=? AND status='ATTEMPT_STARTED'",
            (message, at, account_id, task_id),
        )


def _temporary_upstream_failure(exc):
    if isinstance(exc, UpstreamUnavailable):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "request failed", "timed out", "temporary backoff", "temporarily",
        "http 429", "http 5", "connection",
        "熔断", "限流", "过于频繁", "invalid json",
        "business error", "unexpected shape",
    ))


def _eligible_accounts():
    all_accounts = list_accounts(include_cookie=True)
    accounts = [account for account in all_accounts if account["auto_enabled"]]
    identity_counts = Counter(str(account.get("campus_user_id") or "").strip() for account in all_accounts)
    return [
        account for account in accounts
        if account.get("session_status") == "VALID"
        and str(account.get("campus_user_id") or "").strip()
        and identity_counts[str(account["campus_user_id"]).strip()] == 1
    ]


def _identity_can_submit(account):
    identity = str(account.get("campus_user_id") or "").strip()
    if account.get("session_status") != "VALID" or not identity:
        return False
    with connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM campus_accounts WHERE campus_user_id=?",
            (identity,),
        ).fetchone()[0] == 1


def _upstream_ready():
    with _upstream_lock:
        return time.monotonic() >= _upstream_retry_after


def _defer_upstream():
    global _upstream_retry_after
    with _upstream_lock:
        _upstream_retry_after = max(_upstream_retry_after, time.monotonic() + UPSTREAM_RETRY_SECONDS)


def _clear_upstream_backoff():
    global _upstream_retry_after
    with _upstream_lock:
        _upstream_retry_after = 0


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
        cursor = db.execute(
            "INSERT OR IGNORE INTO checkins(date,task_id,task_name,start_time,end_time,submit_time,status,response_message,created_at,updated_at,account_id,account_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (at[:10], task.task_id, task.name, task.start_time, task.end_time, at, status, message, at, at, account["id"], account["name"]),
        )
    return cursor.rowcount > 0


def start_scheduler():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, name="campustoday-scheduler", daemon=True).start()


def _loop():
    global _next_run
    interval = max(5, int(os.getenv("QUERY_INTERVAL_MINUTES", "5"))) * 60
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
