import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from campus.client import create_client
from .db import get_setting, log_event, now_iso, set_settings

_lock = threading.Lock()
_started = False
_next_run = None


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
    try:
        client = create_client()
        tasks = client.list_today()
        log_event("POLL_OK", f"Task poll completed; {len(tasks)} task(s) returned")
    except Exception as exc:
        # Expected until the institution-specific current protocol is configured.
        log_event("POLL_SKIPPED", str(exc), "WARNING")


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
