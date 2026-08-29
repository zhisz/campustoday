import hmac
import math
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from .campus_accounts import check_session, create_account, delete_account, device_defaults, get_account, list_accounts, update_account
from .dashboard import build_dashboard, invalidate_school_cache
from .db import connect, get_setting, log_event, migrate, now_iso, set_settings
from .location import verify_location
from .mobile_api import load_releases, mobile_api
from .scheduler import start_scheduler, status as scheduler_status
from .templates import ACCOUNTS, ANNOUNCEMENTS, APP_USERS, BASE, DASHBOARD, FEEDBACK_ADMIN, LOGIN, MOBILE_LANDING, SETTINGS, TABLE


def create_app():
    # The SQLite database contains attendance session cookies. Keep all files
    # created by the web process private to its operating-system user.
    os.umask(0o077)
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.secret_key = os.environ["APP_SECRET"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True, MAX_CONTENT_LENGTH=32 * 1024)
    migrate()
    _ensure_admin()
    start_scheduler()
    app.register_blueprint(mobile_api)

    @app.context_processor
    def helpers():
        def csrf_token():
            session.setdefault("csrf", secrets.token_urlsafe(24))
            return session["csrf"]
        return {"csrf_token": csrf_token}

    @app.before_request
    def csrf_check():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "location_proof" and not request.path.startswith("/api/v1/"):
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

    @app.get("/app")
    def mobile_landing():
        releases = load_releases()
        return render_template_string(MOBILE_LANDING, latest=releases[0] if releases else None)

    @app.get("/download/<path:filename>")
    def mobile_download(filename):
        if filename not in {item.get("filename") for item in load_releases()}:
            abort(404)
        return send_from_directory(os.getenv("APKS_PATH", "/data/apks"), filename, as_attachment=True)

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
        return page("状态", DASHBOARD, status=scheduler_status(), dashboard=build_dashboard(request.args.get("account", type=int)))

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

    @app.get("/accounts")
    @protected
    def campus_accounts():
        return page("签到账号", ACCOUNTS, accounts=list_accounts(), device_defaults=device_defaults())

    @app.get("/app-users")
    @protected
    def app_users():
        with connect() as db:
            users = db.execute(
                "SELECT u.id,u.username,u.status,u.last_login_at,u.created_at,COUNT(a.id) AS account_count "
                "FROM app_users u LEFT JOIN campus_accounts a ON a.owner_user_id=u.id GROUP BY u.id ORDER BY u.id DESC"
            ).fetchall()
        return page("App 用户", APP_USERS, users=users)

    @app.post("/app-users/<int:user_id>/toggle")
    @protected
    def app_user_toggle(user_id):
        status = request.form.get("status")
        if status not in {"ACTIVE", "DISABLED"}:
            abort(400)
        with connect() as db:
            if not db.execute("SELECT 1 FROM app_users WHERE id=?", (user_id,)).fetchone():
                abort(404)
            db.execute("UPDATE app_users SET status=?,updated_at=? WHERE id=?", (status, now_iso(), user_id))
            if status == "DISABLED":
                db.execute("UPDATE app_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id))
        flash("用户状态已更新")
        return redirect(url_for("app_users"))

    @app.route("/announcements", methods=["GET", "POST"])
    @protected
    def announcements():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            try:
                starts_at = datetime.fromisoformat(request.form.get("starts_at", "")).replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
                ends_at = datetime.fromisoformat(request.form.get("ends_at", "")).replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
            except ValueError:
                abort(400)
            if not title or len(title) > 120 or not content or len(content) > 2000 or ends_at <= starts_at:
                abort(400)
            with connect() as db:
                db.execute(
                    "INSERT INTO announcements(title,content,published,created_at,starts_at,ends_at) VALUES(?,?,1,?,?,?)",
                    (title, content, now_iso(), starts_at.isoformat(timespec="seconds"), ends_at.isoformat(timespec="seconds")),
                )
            log_event("ANNOUNCEMENT_PUBLISHED", "Administrator published an announcement")
            flash("公告已推送给所有 App 用户")
            return redirect(url_for("announcements"))
        with connect() as db:
            rows = db.execute(
                "SELECT a.*,COUNT(r.user_id) AS read_count FROM announcements a "
                "LEFT JOIN announcement_reads r ON r.announcement_id=a.id GROUP BY a.id ORDER BY a.id DESC"
            ).fetchall()
            user_count = db.execute("SELECT COUNT(*) FROM app_users WHERE status='ACTIVE'").fetchone()[0]
        current = datetime.now(ZoneInfo("Asia/Shanghai"))
        announcements_view = []
        for row in rows:
            item = dict(row)
            for source, target in (("starts_at", "starts_display"), ("ends_at", "ends_display")):
                value = item.get(source)
                item[target] = datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M") if value else "长期有效"
            announcements_view.append(item)
        return page("公告", ANNOUNCEMENTS, announcements=announcements_view, user_count=user_count,
                    default_start=current.strftime("%Y-%m-%dT%H:%M"), default_end=(current + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"), now=now_iso())

    @app.post("/announcements/<int:announcement_id>/withdraw")
    @protected
    def announcement_withdraw(announcement_id):
        with connect() as db:
            changed = db.execute("UPDATE announcements SET published=0 WHERE id=?", (announcement_id,)).rowcount
        if not changed:
            abort(404)
        flash("公告已撤回")
        return redirect(url_for("announcements"))

    @app.post("/announcements/<int:announcement_id>/delete")
    @protected
    def announcement_delete(announcement_id):
        with connect() as db:
            changed = db.execute("DELETE FROM announcements WHERE id=?", (announcement_id,)).rowcount
        if not changed:
            abort(404)
        flash("历史公告已永久删除")
        return redirect(url_for("announcements"))

    @app.get("/feedback")
    @protected
    def feedback():
        with connect() as db:
            rows = db.execute(
                "SELECT f.*,u.username FROM feedback f JOIN app_users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 200"
            ).fetchall()
        return page("反馈", FEEDBACK_ADMIN, feedback=rows)

    @app.post("/feedback/<int:feedback_id>/status")
    @protected
    def feedback_status(feedback_id):
        status = request.form.get("status")
        if status not in {"OPEN", "RESOLVED"}:
            abort(400)
        with connect() as db:
            changed = db.execute("UPDATE feedback SET status=?,updated_at=? WHERE id=?", (status, now_iso(), feedback_id)).rowcount
        if not changed:
            abort(404)
        flash("反馈状态已更新")
        return redirect(url_for("feedback"))

    @app.post("/accounts")
    @protected
    def campus_account_create():
        try:
            account_id = create_account(
                request.form.get("name"),
                request.form.get("session_cookie"),
                request.form.get("auto_enabled") == "true",
                _device_form(request.form),
            )
            result = check_session(account_id)
            flash(f"账号已添加，已识别登录用户：{result['real_name']}" if result["valid"] else "账号已添加，但 Cookie 验证失败")
            log_event("CAMPUS_ACCOUNT_CREATED", f"Campus account {account_id} created")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("campus_accounts"))

    @app.post("/accounts/<int:account_id>/update")
    @protected
    def campus_account_update(account_id):
        try:
            changed = update_account(
                account_id,
                request.form.get("name"),
                request.form.get("session_cookie"),
                request.form.get("auto_enabled") == "true",
                _device_form(request.form),
            )
            if not changed:
                abort(404)
            invalidate_school_cache(account_id)
            flash("账号设置已保存；如果更换了 Cookie，请点击“检测会话”")
            log_event("CAMPUS_ACCOUNT_UPDATED", f"Campus account {account_id} updated")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("campus_accounts"))

    @app.post("/accounts/<int:account_id>/check")
    @protected
    def campus_account_check(account_id):
        if not get_account(account_id):
            abort(404)
        result = check_session(account_id)
        if result.get("cached"):
            flash(f"检测过于频繁，已使用 60 秒内的最近结果：{result.get('real_name') or '未识别'}")
        else:
            flash(f"会话有效，登录用户：{result['real_name']}" if result["valid"] else f"会话检测失败：{result['error']}")
        log_event("CAMPUS_SESSION_CHECKED", f"Campus account {account_id}: {'valid' if result['valid'] else 'invalid'}")
        if request.form.get("next") == "dashboard":
            return redirect(url_for("dashboard", account=account_id))
        return redirect(url_for("campus_accounts"))

    @app.post("/accounts/<int:account_id>/toggle")
    @protected
    def campus_account_toggle(account_id):
        account = get_account(account_id, include_cookie=True)
        if not account:
            abort(404)
        update_account(account_id, account["name"], "", request.form.get("enabled") == "true")
        log_event("CAMPUS_ACCOUNT_AUTOMATION", f"Campus account {account_id} automation updated")
        flash("该账号的自动签到已更新")
        return redirect(url_for("dashboard", account=account_id))

    @app.post("/accounts/<int:account_id>/delete")
    @protected
    def campus_account_delete(account_id):
        if not delete_account(account_id):
            abort(404)
        invalidate_school_cache(account_id)
        log_event("CAMPUS_ACCOUNT_DELETED", f"Campus account {account_id} deleted")
        flash("签到账号已删除，历史执行记录仍保留")
        return redirect(url_for("campus_accounts"))

    @app.get("/history")
    @protected
    def history():
        with connect() as db:
            records = db.execute("SELECT date,account_name,task_name,status,submit_time,response_message FROM checkins ORDER BY id DESC LIMIT 30").fetchall()
        rows = [[r[k] or "—" for k in ("date", "account_name", "task_name", "status", "submit_time", "response_message")] for r in records]
        return page("历史", TABLE, heading="最近 30 条记录", headers=["日期","账号","任务","状态","提交时间","结果"], rows=rows)

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


def _device_form(form):
    return {key: form.get(key) for key in ("device_id", "device_model", "system_name", "system_version")}
