import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from campus.client import create_client
from .db import get_setting, log_event, now_iso, set_settings

scheduler = BackgroundScheduler(timezone=os.getenv("TZ", "Asia/Shanghai"))


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
    if scheduler.running:
        return
    interval = max(1, int(os.getenv("QUERY_INTERVAL_MINUTES", "5")))
    scheduler.add_job(poll, "interval", minutes=interval, id="attendance_poll", max_instances=1, coalesce=True)
    scheduler.start()


def status():
    job = scheduler.get_job("attendance_poll")
    return {
        "running": scheduler.running,
        "enabled": enabled(),
        "last_check": get_setting("last_check"),
        "last_success": get_setting("last_success"),
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "integration": os.getenv("CPDAILY_MODE", "disabled"),
    }

