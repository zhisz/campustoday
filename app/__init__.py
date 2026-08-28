import hmac
import math
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .dashboard import build_dashboard
from .db import connect, get_setting, log_event, migrate, now_iso, set_settings
from .location import verify_location
from .scheduler import start_scheduler, status as scheduler_status
from .templates import BASE, DASHBOARD, LOGIN, SETTINGS, TABLE


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["APP_SECRET"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True, MAX_CONTENT_LENGTH=32 * 1024)
    migrate()
    _ensure_admin()
    start_scheduler()

    @app.context_processor
    def helpers():
        def csrf_token():
            session.setdefault("csrf", secrets.token_urlsafe(24))
            return session["csrf"]
        return {"csrf_token": csrf_token}

    @app.before_request
    def csrf_check():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "location_proof":
            if not hmac.compare_digest(session.get("csrf", ""), request.form.get("csrf", "")):
                abort(400)

    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("user"):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapped

    def page(title, content, **context):
        body = render_template_string(content, **context)
        return render_template_string(BASE, title=title, content=body)

    @app.get("/health")
    def health():
        try:
            with connect() as db:
                db.execute("SELECT 1").fetchone()
            return jsonify(status="ok")
        except Exception:
            return jsonify(status="error"), 503

    @app.get("/")
    def index():
        return redirect(url_for("dashboard") if session.get("user") else url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            with connect() as db:
                row = db.execute("SELECT username,password_hash FROM accounts WHERE id=1").fetchone()
            if row and hmac.compare_digest(request.form.get("username", ""), row["username"]) and check_password_hash(row["password_hash"], request.form.get("password", "")):
                session.clear(); session["user"] = row["username"]; session["csrf"] = secrets.token_urlsafe(24)
                log_event("ADMIN_LOGIN", "Administrator login succeeded")
                return redirect(url_for("dashboard"))
            log_event("ADMIN_LOGIN_FAILED", "Administrator login failed", "WARNING")
            flash("用户名或密码错误")
        return page("登录", LOGIN)

    @app.post("/logout")
    @protected
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @protected
    def dashboard():
        return page("状态", DASHBOARD, status=scheduler_status(), dashboard=build_dashboard())

    @app.route("/settings", methods=["GET", "POST"])
    @protected
    def settings():
        if request.method == "POST":
            start, end = request.form.get("monitor_start", ""), request.form.get("monitor_end", "")
            try:
                datetime.strptime(start, "%H:%M"); datetime.strptime(end, "%H:%M")
            except ValueError:
                abort(400)
            set_settings({"auto_enabled": "true" if request.form.get("auto_enabled") == "true" else "false", "monitor_start": start, "monitor_end": end})
            log_event("SETTINGS_UPDATED", "Runtime settings updated")
            flash("设置已保存")
            return redirect(url_for("settings"))
        values = {"monitor_start": get_setting("monitor_start", os.getenv("MONITOR_START", "20:00")), "monitor_end": get_setting("monitor_end", os.getenv("MONITOR_END", "23:30"))}
        return page("设置", SETTINGS, values=values, status=scheduler_status())

    @app.get("/history")
    @protected
    def history():
        with connect() as db:
            records = db.execute("SELECT date,task_name,status,submit_time,response_message FROM checkins ORDER BY id DESC LIMIT 30").fetchall()
        rows = [[r[k] or "—" for k in ("date", "task_name", "status", "submit_time", "response_message")] for r in records]
        return page("历史", TABLE, heading="最近 30 条记录", headers=["日期","任务","状态","提交时间","结果"], rows=rows)

    @app.get("/logs")
    @protected
    def logs():
        with connect() as db:
            records = db.execute("SELECT created_at,level,event,message FROM logs ORDER BY id DESC LIMIT 100").fetchall()
        rows = [[r[k] for k in ("created_at", "level", "event", "message")] for r in records]
        return page("日志", TABLE, heading="审计日志", headers=["时间","级别","事件","内容"], rows=rows)

    @app.get("/api/status")
    @protected
    def api_status():
        result = scheduler_status(); result["database"] = "ok"
        return jsonify(result)

    @app.post("/api/location/proof")
    def location_proof():
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        expected = os.getenv("LOCATION_PROOF_TOKEN", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            abort(401)
        data = request.get_json(silent=True) or {}
        try:
            parsed_proof_id = uuid.UUID(str(data["proof_id"]))
            if parsed_proof_id.version != 4:
                raise ValueError
            proof_id = str(parsed_proof_id)
            lat, lon = float(data["latitude"]), float(data["longitude"])
            accuracy = float(data.get("accuracy", 0))
            observed_at = str(data["observed_at"])
            address = str(data.get("address") or "")[:500]
            coordinate_system = str(data["coordinate_system"]).lower()
            if coordinate_system not in {"wgs84", "gcj02"}:
                raise ValueError
            if not all(math.isfinite(value) for value in (lat, lon, accuracy)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            abort(400)
        verified, reason = verify_location(lat, lon, observed_at, accuracy)
        try:
            with connect() as db:
                db.execute(
                    "INSERT INTO locations(latitude,longitude,accuracy,observed_at,received_at,verified,reason,source,proof_id,device_id,address,coordinate_system) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lat, lon, accuracy, observed_at, now_iso(), int(verified), reason, "trusted_device", proof_id, None, address, coordinate_system),
                )
        except sqlite3.IntegrityError:
            return jsonify(accepted=False, reason="DUPLICATE_PROOF"), 409
        log_event("LOCATION_PROOF", reason, "INFO" if verified else "WARNING")
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            expires = observed + timedelta(seconds=int(os.getenv("LOCATION_MAX_AGE_SECONDS", "300")))
        except ValueError:
            expires = None
        return jsonify(accepted=verified, reason=reason, expires_at=expires.isoformat() if expires else None), 200 if verified else 422

    return app


def _ensure_admin():
    username, password = os.environ["ADMIN_USERNAME"], os.environ["ADMIN_PASSWORD"]
    at = now_iso()
    with connect() as db:
        row = db.execute("SELECT id FROM accounts WHERE id=1").fetchone()
        if not row:
            db.execute("INSERT INTO accounts(id,username,password_hash,created_at,updated_at) VALUES(1,?,?,?,?)", (username, generate_password_hash(password, method="pbkdf2:sha256:600000"), at, at))
            db.execute("INSERT INTO logs(level,event,message,metadata_json,created_at) VALUES('INFO','DATABASE_INITIALIZED','Initial administrator created','{}',?)", (at,))
