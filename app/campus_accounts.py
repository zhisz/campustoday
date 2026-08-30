from datetime import datetime, timezone

from campus.client import create_client
from campus.device import DeviceProfile

from .db import connect, now_iso


MAX_ACCOUNTS = 20
DEVICE_COLUMNS = "device_id,app_version,device_model,system_name,system_version"


def list_accounts(include_cookie=False, owner_user_id=None):
    columns = f"id,name,real_name,campus_user_id,identity_verified_at,owner_user_id,auto_enabled,session_status,last_checked_at,last_error,created_at,updated_at,{DEVICE_COLUMNS},(SELECT username FROM app_users WHERE id=campus_accounts.owner_user_id) AS owner_username"
    if include_cookie:
        columns += ",session_cookie"
    with connect() as db:
        if owner_user_id is None:
            rows = db.execute(f"SELECT {columns} FROM campus_accounts ORDER BY id").fetchall()
        else:
            rows = db.execute(f"SELECT {columns} FROM campus_accounts WHERE owner_user_id=? ORDER BY id", (owner_user_id,)).fetchall()
    return [dict(row) for row in rows]


def get_account(account_id, include_cookie=False):
    columns = f"id,name,real_name,campus_user_id,identity_verified_at,owner_user_id,auto_enabled,session_status,last_checked_at,last_error,created_at,updated_at,{DEVICE_COLUMNS},(SELECT username FROM app_users WHERE id=campus_accounts.owner_user_id) AS owner_username"
    if include_cookie:
        columns += ",session_cookie"
    with connect() as db:
        row = db.execute(f"SELECT {columns} FROM campus_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def create_account(name, session_cookie, auto_enabled, device=None, owner_user_id=None):
    name, session_cookie = _validate(name, session_cookie)
    device_values = device_defaults()
    device_values.update({key: value for key, value in (device or {}).items() if value is not None})
    device = _validate_device(device_values)
    at = now_iso()
    with connect() as db:
        if owner_user_id is None:
            count = db.execute("SELECT COUNT(*) FROM campus_accounts WHERE owner_user_id IS NULL").fetchone()[0]
        else:
            count = db.execute("SELECT COUNT(*) FROM campus_accounts WHERE owner_user_id=?", (owner_user_id,)).fetchone()[0]
        if count >= MAX_ACCOUNTS:
            raise ValueError(f"最多可添加 {MAX_ACCOUNTS} 个签到账号")
        cursor = db.execute(
            "INSERT INTO campus_accounts(name,session_cookie,auto_enabled,created_at,updated_at,device_id,app_version,device_model,system_name,system_version,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (name, session_cookie, int(auto_enabled), at, at, device["device_id"], device["app_version"], device["device_model"], device["system_name"], device["system_version"], owner_user_id),
        )
        return cursor.lastrowid


def update_account(account_id, name, session_cookie, auto_enabled, device=None):
    existing = get_account(account_id, include_cookie=True)
    if not existing:
        return False
    if existing.get("real_name"):
        name = existing["real_name"]
    cookie = session_cookie.strip() if session_cookie and session_cookie.strip() else existing["session_cookie"]
    name, cookie = _validate(name, cookie)
    device_values = {key: existing.get(key) for key in DEVICE_COLUMNS.split(",")}
    device_values.update({key: value for key, value in (device or {}).items() if value is not None})
    device = _validate_device(device_values)
    cookie_changed = cookie != existing["session_cookie"]
    with connect() as db:
        db.execute(
            "UPDATE campus_accounts SET name=?,session_cookie=?,auto_enabled=?,session_status=?,last_checked_at=?,last_error=?,updated_at=?,device_id=?,app_version=?,device_model=?,system_name=?,system_version=?,real_name=?,campus_user_id=?,identity_verified_at=? WHERE id=?",
            (
                name,
                cookie,
                int(auto_enabled),
                "UNKNOWN" if cookie_changed else existing["session_status"],
                None if cookie_changed else existing["last_checked_at"],
                None if cookie_changed else existing["last_error"],
                now_iso(),
                device["device_id"],
                device["app_version"],
                device["device_model"],
                device["system_name"],
                device["system_version"],
                None if cookie_changed else existing["real_name"],
                None if cookie_changed else existing["campus_user_id"],
                None if cookie_changed else existing["identity_verified_at"],
                account_id,
            ),
        )
    return True


def delete_account(account_id):
    with connect() as db:
        return db.execute("DELETE FROM campus_accounts WHERE id=?", (account_id,)).rowcount > 0


def check_session(account_id):
    account = get_account(account_id, include_cookie=True)
    if not account:
        raise LookupError("签到账号不存在")
    last_checked = _parse_timestamp(account.get("last_checked_at"))
    if last_checked and account.get("real_name") and (datetime.now(timezone.utc) - last_checked).total_seconds() < 60:
        valid = account["session_status"] == "VALID"
        return {"valid": valid, "cached": True, "error": account.get("last_error"), "real_name": account.get("real_name")}
    at = now_iso()
    identity = None
    try:
        identity = create_client(account["session_cookie"]).identity()
        status, error = "VALID", None
        result = {"valid": True, "real_name": identity["user_name"], "cached": False}
    except Exception as exc:
        raw_error = _safe_error(exc)
        if _is_definitive_auth_failure(exc):
            status, error = "INVALID", raw_error
            result = {"valid": False, "error": error, "cached": False}
        else:
            previously_valid = account["session_status"] == "VALID" or (
                bool(account.get("real_name")) and _is_transient_error(account.get("last_error"))
            )
            status = "VALID" if previously_valid else "UNKNOWN"
            error = (
                "学校接口暂时不可用，已保留上次有效状态"
                if previously_valid
                else "学校接口暂时不可用，请稍后重试"
            )
            result = {
                "valid": True if previously_valid else None,
                "error": error,
                "cached": False,
                "temporary": True,
            }
    with connect() as db:
        db.execute(
            "UPDATE campus_accounts SET session_status=?,last_checked_at=?,last_error=?,updated_at=?,name=COALESCE(?,name),real_name=?,campus_user_id=?,identity_verified_at=? WHERE id=?",
            (
                status,
                at,
                error,
                at,
                identity["user_name"] if identity else None,
                identity["user_name"] if identity else account.get("real_name"),
                identity["user_id"] if identity else account.get("campus_user_id"),
                at if status == "VALID" else account.get("identity_verified_at"),
                account_id,
            ),
        )
    return result


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _validate(name, session_cookie):
    name = str(name or "").strip()
    session_cookie = str(session_cookie or "").strip()
    if not name or len(name) > 80:
        raise ValueError("账号名称必须为 1–80 个字符")
    if session_cookie and "=" not in session_cookie:
        session_cookie = f"MOD_AUTH_CAS={session_cookie}"
    if not session_cookie or len(session_cookie) > 12_000:
        raise ValueError("Cookie 格式无效")
    if "\r" in session_cookie or "\n" in session_cookie:
        raise ValueError("Cookie 不得包含换行符")
    return name, session_cookie


def device_defaults():
    import os
    return {
        "device_id": os.getenv("CPDAILY_DEVICE_ID", "").strip(),
        "app_version": os.getenv("CPDAILY_APP_VERSION", "").strip(),
        "device_model": os.getenv("CPDAILY_DEVICE_MODEL", "").strip(),
        "system_name": os.getenv("CPDAILY_SYSTEM_NAME", "").strip(),
        "system_version": os.getenv("CPDAILY_SYSTEM_VERSION", "").strip(),
    }


def account_device(account):
    values = _validate_device({key: account.get(key) for key in DEVICE_COLUMNS.split(",")})
    return DeviceProfile(
        device_id=values["device_id"],
        app_version=values["app_version"],
        model=values["device_model"],
        system_name=values["system_name"],
        system_version=values["system_version"],
    )


def _validate_device(device):
    values = {key: str((device or {}).get(key) or "").strip() for key in DEVICE_COLUMNS.split(",")}
    required = ("device_id", "device_model", "system_name", "system_version")
    if any(not values[key] for key in required):
        raise ValueError("设备信息必须完整填写")
    if any(len(value) > 200 or "\r" in value or "\n" in value for value in values.values()):
        raise ValueError("设备信息格式无效")
    return values


def _safe_error(exc):
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in ("cookie", "token", "authorization", "session=")):
        return "认证信息无效或已失效"
    return text[:300]


def _is_definitive_auth_failure(exc):
    text = str(exc).strip().lower()
    return any(marker in text for marker in (
        "portal session is not logged in",
        "portal api returned http 401",
        "portal api returned http 403",
        "认证信息无效或已失效",
    ))


def _is_transient_error(error):
    text = str(error or "").strip().lower()
    return any(marker in text for marker in (
        "request failed",
        "timed out",
        "temporarily",
        "暂时不可用",
    ))
