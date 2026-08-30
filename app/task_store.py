from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import connect, now_iso


MAX_TASKS_PER_ACCOUNT = 100
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
HISTORY_GROUPS = (
    "signedTasks",
    "codeRcvdTasks",
    "registerLeaveTasks",
    "leaveTasks",
    "unSignedTasks",
)


def upsert_account_tasks(account_id, tasks):
    """Persist one trusted daily-list snapshot without deleting older records."""
    seen_at = now_iso()
    rows = []
    for task in tasks:
        task_id = str(task.task_id or "").strip()
        if not task_id:
            continue
        start_time = str(task.start_time or "")
        end_time = str(task.end_time or "")
        source_group = str(getattr(task, "source_group", "") or "")
        rows.append((
            account_id,
            task_id,
            str(task.sign_wid or ""),
            str(task.name or ""),
            start_time,
            end_time,
            int(bool(task.completed)),
            1,
            str(task.status or ""),
            source_group,
            _record_date(start_time, end_time, seen_at),
            "",
            "",
            seen_at,
            seen_at,
            _sort_at(end_time, start_time, seen_at),
            seen_at if task.completed else None,
        ))
    _upsert_rows(account_id, rows, seen_at, sync_kind="tasks")
    return len(rows)


def upsert_account_history(account_id, history, year_month=None):
    """Merge a monthly school-history response into the same cloud record set."""
    seen_at = now_iso()
    rows = []
    source_rows = history.get("rows") if isinstance(history, dict) else None
    if not isinstance(source_rows, list):
        raise ValueError("Attendance history contains no rows")
    for day in source_rows:
        if not isinstance(day, dict):
            continue
        record_date = _history_date(day.get("dayInMonth"), year_month)
        for group in HISTORY_GROUPS:
            items = day.get(group) or []
            if not isinstance(items, list):
                continue
            completed = group != "unSignedTasks"
            for item in items:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("signInstanceWid") or "").strip()
                if not task_id:
                    continue
                start_time = str(item.get("singleTaskBeginTime") or item.get("rateTaskBeginTime") or "")
                end_time = str(item.get("singleTaskEndTime") or item.get("rateTaskEndTime") or "")
                signed_time = str(item.get("rateSignDate") or "") if completed else ""
                effective_date = record_date or _record_date(start_time, end_time, signed_time or seen_at)
                rows.append((
                    account_id,
                    task_id,
                    str(item.get("signWid") or ""),
                    str(item.get("taskName") or ""),
                    start_time,
                    end_time,
                    int(completed),
                    int(effective_date == datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")),
                    str(item.get("signStatus") or group),
                    group,
                    effective_date,
                    str(item.get("senderUserName") or ""),
                    signed_time,
                    seen_at,
                    seen_at,
                    _sort_at(signed_time, end_time, start_time, effective_date, seen_at),
                    signed_time or (seen_at if completed else None),
                ))
    _upsert_rows(account_id, rows, seen_at, sync_kind="history")
    return len(rows)


def _upsert_rows(account_id, rows, seen_at, sync_kind):
    with connect() as db:
        if rows:
            db.executemany(
                "INSERT INTO account_task_records("
                "account_id,task_id,sign_wid,task_name,start_time,end_time,completed,"
                "active_today,school_status,source_group,record_date,publisher,signed_time,"
                "first_seen_at,last_seen_at,sort_at,completed_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id,task_id) DO UPDATE SET "
                "sign_wid=COALESCE(NULLIF(excluded.sign_wid,''),account_task_records.sign_wid),"
                "task_name=COALESCE(NULLIF(excluded.task_name,''),account_task_records.task_name),"
                "start_time=COALESCE(NULLIF(excluded.start_time,''),account_task_records.start_time),"
                "end_time=COALESCE(NULLIF(excluded.end_time,''),account_task_records.end_time),"
                "completed=MAX(account_task_records.completed,excluded.completed),"
                "active_today=MAX(account_task_records.active_today,excluded.active_today),"
                "school_status=CASE WHEN account_task_records.completed=1 AND excluded.completed=0 "
                "THEN account_task_records.school_status ELSE COALESCE(NULLIF(excluded.school_status,''),account_task_records.school_status) END,"
                "source_group=CASE WHEN account_task_records.completed=1 AND excluded.completed=0 "
                "THEN account_task_records.source_group ELSE COALESCE(NULLIF(excluded.source_group,''),account_task_records.source_group) END,"
                "record_date=COALESCE(NULLIF(excluded.record_date,''),account_task_records.record_date),"
                "publisher=COALESCE(NULLIF(excluded.publisher,''),account_task_records.publisher),"
                "signed_time=COALESCE(NULLIF(excluded.signed_time,''),account_task_records.signed_time),"
                "last_seen_at=excluded.last_seen_at,sort_at=MAX(account_task_records.sort_at,excluded.sort_at),"
                "completed_at=CASE WHEN excluded.completed=1 "
                "THEN COALESCE(NULLIF(excluded.completed_at,''),account_task_records.completed_at) "
                "ELSE account_task_records.completed_at END",
                rows,
            )
        if sync_kind == "history":
            db.execute(
                "INSERT INTO account_task_sync_state("
                "account_id,synced_at,history_synced_at,task_count,last_error,updated_at"
                ") VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
                "synced_at=excluded.synced_at,history_synced_at=excluded.history_synced_at,"
                "last_error=NULL,updated_at=excluded.updated_at",
                (account_id, seen_at, seen_at, 0, None, seen_at),
            )
        else:
            db.execute(
                "INSERT INTO account_task_sync_state(account_id,synced_at,task_count,last_error,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
                "synced_at=excluded.synced_at,task_count=excluded.task_count,last_error=NULL,updated_at=excluded.updated_at",
                (account_id, seen_at, len(rows), None, seen_at),
            )
        db.execute(
            "DELETE FROM account_task_records WHERE account_id=? AND id NOT IN ("
            "SELECT id FROM account_task_records WHERE account_id=? "
            "ORDER BY sort_at DESC,id DESC LIMIT ?)",
            (account_id, account_id, MAX_TASKS_PER_ACCOUNT),
        )


def list_account_tasks(account_id, limit=MAX_TASKS_PER_ACCOUNT):
    limit = max(1, min(int(limit), MAX_TASKS_PER_ACCOUNT))
    with connect() as db:
        rows = db.execute(
            "SELECT task_id,sign_wid,task_name,start_time,end_time,completed,active_today,"
            "school_status,source_group,record_date,publisher,signed_time,"
            "first_seen_at,last_seen_at,sort_at,completed_at "
            "FROM account_task_records WHERE account_id=? "
            "ORDER BY sort_at DESC,id DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def task_sync_state(account_id):
    with connect() as db:
        row = db.execute(
            "SELECT synced_at,history_synced_at,task_count,last_error,updated_at "
            "FROM account_task_sync_state WHERE account_id=?",
            (account_id,),
        ).fetchone()
    return dict(row) if row else {
        "synced_at": None,
        "history_synced_at": None,
        "task_count": 0,
        "last_error": None,
        "updated_at": None,
    }


def latest_task_sync(account_id):
    return task_sync_state(account_id)["synced_at"]


def history_sync_due(account_id, interval_minutes=None):
    if interval_minutes is None:
        interval_minutes = 360
    last_value = task_sync_state(account_id)["history_synced_at"]
    last_sync = _parse_time(last_value)
    return not last_sync or datetime.now(timezone.utc) - last_sync >= timedelta(minutes=max(30, int(interval_minutes)))


def record_task_sync_error(account_id, error):
    at = now_iso()
    message = str(error or "学校数据同步失败")[:300]
    with connect() as db:
        db.execute(
            "INSERT INTO account_task_sync_state(account_id,task_count,last_error,updated_at) "
            "VALUES(?,0,?,?) ON CONFLICT(account_id) DO UPDATE SET "
            "last_error=excluded.last_error,updated_at=excluded.updated_at",
            (account_id, message, at),
        )


def _history_date(value, year_month):
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    if year_month and text.isdigit():
        return f"{year_month}-{text.zfill(2)}"
    return ""


def _record_date(*values):
    for value in values:
        parsed = _parse_time(value)
        if parsed:
            return parsed.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        text = str(value or "").strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
    return ""


def _sort_at(*values):
    for value in values:
        parsed = _parse_time(value)
        if parsed:
            return parsed.isoformat(timespec="seconds")
        text = str(value or "").strip()
        if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
            parsed = datetime.fromisoformat(text).replace(tzinfo=LOCAL_TZ)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    return now_iso()


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(timezone.utc)
