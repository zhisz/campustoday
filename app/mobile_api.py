import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .campus_accounts import check_session, create_account, delete_account, get_account, list_accounts, update_account
from .dashboard import build_dashboard, invalidate_school_cache
from .db import connect, now_iso


mobile_api = Blueprint("mobile_api", __name__, url_prefix="/api/v1")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{3,40}$")


def _json_error(message, status=400):
    return jsonify(error=message), status


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_token(user_id):
    token = secrets.token_urlsafe(36)
    at = datetime.now(timezone.utc)
    expires = at + timedelta(days=180)
    with connect() as db:
        db.execute(
            "INSERT INTO app_tokens(user_id,token_hash,created_at,last_used_at,expires_at) VALUES(?,?,?,?,?)",
            (user_id, _token_hash(token), at.isoformat(timespec="seconds"), at.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
        )
    return token, expires.isoformat(timespec="seconds")


def app_login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token:
            return _json_error("请先登录", 401)
        now = datetime.now(timezone.utc)
        with connect() as db:
            row = db.execute(
                "SELECT t.id AS token_id,t.last_used_at,t.expires_at,u.id,u.username,u.status "
                "FROM app_tokens t JOIN app_users u ON u.id=t.user_id "
                "WHERE t.token_hash=? AND t.revoked_at IS NULL",
                (_token_hash(token),),
            ).fetchone()
            if not row or row["status"] != "ACTIVE":
                return _json_error("登录已失效", 401)
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires <= now:
                return _json_error("登录已过期", 401)
            last_used = datetime.fromisoformat(row["last_used_at"].replace("Z", "+00:00"))
            if (now - last_used).total_seconds() >= 300:
                db.execute("UPDATE app_tokens SET last_used_at=? WHERE id=?", (now_iso(), row["token_id"]))
        g.app_user = {"id": row["id"], "username": row["username"]}
        g.app_token = token
        return fn(*args, **kwargs)
    return wrapped


@mobile_api.post("/auth/register")
def register():
    if os.getenv("APP_REGISTRATION_ENABLED", "true").lower() != "true":
        return _json_error("注册暂未开放", 403)
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not USERNAME_RE.fullmatch(username):
        return _json_error("用户名需为 3–40 位中文、字母、数字、下划线或连字符")
    if len(password) < 8 or len(password) > 128:
        return _json_error("密码需为 8–128 个字符")
    at = now_iso()
    try:
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO app_users(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
                (username, generate_password_hash(password, method="pbkdf2:sha256:600000"), at, at),
            )
            user_id = cursor.lastrowid
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            return _json_error("用户名已存在", 409)
        raise
    token, expires = _issue_token(user_id)
    return jsonify(token=token, expires_at=expires, user={"id": user_id, "username": username}), 201


@mobile_api.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    with connect() as db:
        row = db.execute("SELECT id,username,password_hash,status FROM app_users WHERE username=?", (username,)).fetchone()
    if not row or row["status"] != "ACTIVE" or not check_password_hash(row["password_hash"], password):
        return _json_error("用户名或密码错误", 401)
    token, expires = _issue_token(row["id"])
    with connect() as db:
        db.execute("UPDATE app_users SET last_login_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
    return jsonify(token=token, expires_at=expires, user={"id": row["id"], "username": row["username"]})


@mobile_api.post("/auth/logout")
@app_login_required
def logout():
    with connect() as db:
        db.execute("UPDATE app_tokens SET revoked_at=? WHERE token_hash=?", (now_iso(), _token_hash(g.app_token)))
    return jsonify(ok=True)


@mobile_api.get("/me")
@app_login_required
def me():
    return jsonify(user=g.app_user)


def _account_summary(account):
    return {
        "id": account["id"], "name": account["real_name"] or account["name"],
        "identity_verified": bool(account["real_name"]), "auto_enabled": bool(account["auto_enabled"]),
        "session_status": account["session_status"], "last_checked_at": account["last_checked_at"],
        "last_error": account["last_error"],
        "device": {"device_id": account["device_id"], "model": account["device_model"],
                   "system_name": account["system_name"], "system_version": account["system_version"],
                   "app_version": account["app_version"]},
    }


def _owned_account(account_id, include_cookie=False):
    account = get_account(account_id, include_cookie=include_cookie)
    return account if account and account.get("owner_user_id") == g.app_user["id"] else None


@mobile_api.get("/accounts")
@app_login_required
def accounts_list():
    accounts = list_accounts(owner_user_id=g.app_user["id"])
    return jsonify(accounts=[_account_summary(account) for account in accounts])


@mobile_api.post("/accounts")
@app_login_required
def accounts_create():
    data = request.get_json(silent=True) or {}
    device = data.get("device") if isinstance(data.get("device"), dict) else {}
    try:
        account_id = create_account(
            "待识别账号", data.get("session_cookie"), False,
            {"device_id": device.get("device_id"), "app_version": device.get("app_version"),
             "device_model": device.get("model"), "system_name": device.get("system_name"),
             "system_version": device.get("system_version")},
            owner_user_id=g.app_user["id"],
        )
    except ValueError as exc:
        return _json_error(str(exc))
    result = check_session(account_id)
    if result.get("valid"):
        account_id, conflict = _merge_duplicate_identity(account_id, g.app_user["id"])
        if conflict:
            return _json_error("该学校账号已被其他 App 用户添加", 409)
    return jsonify(account=_account_summary(get_account(account_id)), check=result), 201


def _merge_duplicate_identity(new_account_id, owner_user_id):
    """Claim a legacy admin account or refresh an account already owned by this user."""
    new_account = get_account(new_account_id, include_cookie=True)
    if not new_account or not new_account.get("campus_user_id"):
        return new_account_id, False
    with connect() as db:
        existing = db.execute(
            "SELECT id,owner_user_id,auto_enabled FROM campus_accounts WHERE campus_user_id=? AND id<>? ORDER BY id LIMIT 1",
            (new_account["campus_user_id"], new_account_id),
        ).fetchone()
        if not existing:
            return new_account_id, False
        if existing["owner_user_id"] not in (None, owner_user_id):
            db.execute("DELETE FROM campus_accounts WHERE id=?", (new_account_id,))
            return new_account_id, True
        auto_enabled = existing["auto_enabled"] if existing["owner_user_id"] == owner_user_id else 0
        db.execute(
            "UPDATE campus_accounts SET name=?,real_name=?,campus_user_id=?,identity_verified_at=?,session_cookie=?,"
            "session_status=?,last_checked_at=?,last_error=?,device_id=?,app_version=?,device_model=?,system_name=?,"
            "system_version=?,owner_user_id=?,auto_enabled=?,updated_at=? WHERE id=?",
            (new_account["name"], new_account["real_name"], new_account["campus_user_id"], new_account["identity_verified_at"],
             new_account["session_cookie"], new_account["session_status"], new_account["last_checked_at"], new_account["last_error"],
             new_account["device_id"], new_account["app_version"], new_account["device_model"], new_account["system_name"],
             new_account["system_version"], owner_user_id, auto_enabled, now_iso(), existing["id"]),
        )
        db.execute("DELETE FROM campus_accounts WHERE id=?", (new_account_id,))
        return existing["id"], False


@mobile_api.get("/accounts/<int:account_id>")
@app_login_required
def account_status(account_id):
    account = _owned_account(account_id)
    if not account:
        return _json_error("账号不存在", 404)
    dashboard = build_dashboard(account_id)
    return jsonify(account=_account_summary(account), tasks=dashboard["upcoming"], history=dashboard["school_history"],
                   month=dashboard["history_month"], signed_count=dashboard["school_signed_count"],
                   automatic_successes=dashboard["automatic_successes"], school_error=dashboard["school_error"])


@mobile_api.post("/accounts/<int:account_id>/check")
@app_login_required
def account_check(account_id):
    if not _owned_account(account_id):
        return _json_error("账号不存在", 404)
    invalidate_school_cache(account_id)
    result = check_session(account_id)
    return jsonify(account=_account_summary(get_account(account_id)), check=result)


@mobile_api.patch("/accounts/<int:account_id>")
@app_login_required
def account_update(account_id):
    account = _owned_account(account_id, include_cookie=True)
    if not account:
        return _json_error("账号不存在", 404)
    enabled = (request.get_json(silent=True) or {}).get("auto_enabled")
    if not isinstance(enabled, bool):
        return _json_error("auto_enabled 必须是布尔值")
    update_account(account_id, account["name"], "", enabled)
    return jsonify(account=_account_summary(get_account(account_id)))


@mobile_api.delete("/accounts/<int:account_id>")
@app_login_required
def account_delete(account_id):
    if not _owned_account(account_id):
        return _json_error("账号不存在", 404)
    delete_account(account_id)
    invalidate_school_cache(account_id)
    return jsonify(ok=True)


def load_releases():
    path = os.path.join(os.getenv("APKS_PATH", "/data/apks"), "releases.json")
    try:
        with open(path, encoding="utf-8") as handle:
            releases = json.load(handle)
    except (OSError, ValueError):
        return []
    return releases if isinstance(releases, list) else []


@mobile_api.get("/app/version")
def app_version():
    releases = load_releases()
    if not releases:
        return _json_error("暂无可用版本", 404)
    latest = dict(releases[0])
    latest["download_url"] = request.url_root.rstrip("/") + "/download/" + latest["filename"]
    return jsonify(latest)
