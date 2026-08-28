from campus.client import create_client

from .db import connect, now_iso


MAX_ACCOUNTS = 20


def list_accounts(include_cookie=False):
    columns = "id,name,auto_enabled,session_status,last_checked_at,last_error,created_at,updated_at"
    if include_cookie:
        columns += ",session_cookie"
    with connect() as db:
        rows = db.execute(f"SELECT {columns} FROM campus_accounts ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_account(account_id, include_cookie=False):
    columns = "id,name,auto_enabled,session_status,last_checked_at,last_error,created_at,updated_at"
    if include_cookie:
        columns += ",session_cookie"
    with connect() as db:
        row = db.execute(f"SELECT {columns} FROM campus_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def create_account(name, session_cookie, auto_enabled):
    name, session_cookie = _validate(name, session_cookie)
    at = now_iso()
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM campus_accounts").fetchone()[0]
        if count >= MAX_ACCOUNTS:
            raise ValueError(f"最多可添加 {MAX_ACCOUNTS} 个签到账号")
        cursor = db.execute(
            "INSERT INTO campus_accounts(name,session_cookie,auto_enabled,created_at,updated_at) VALUES(?,?,?,?,?)",
            (name, session_cookie, int(auto_enabled), at, at),
        )
        return cursor.lastrowid


def update_account(account_id, name, session_cookie, auto_enabled):
    existing = get_account(account_id, include_cookie=True)
    if not existing:
        return False
    cookie = session_cookie.strip() if session_cookie and session_cookie.strip() else existing["session_cookie"]
    name, cookie = _validate(name, cookie)
    cookie_changed = cookie != existing["session_cookie"]
    with connect() as db:
        db.execute(
            "UPDATE campus_accounts SET name=?,session_cookie=?,auto_enabled=?,session_status=?,last_checked_at=?,last_error=?,updated_at=? WHERE id=?",
            (
                name,
                cookie,
                int(auto_enabled),
                "UNKNOWN" if cookie_changed else existing["session_status"],
                None if cookie_changed else existing["last_checked_at"],
                None if cookie_changed else existing["last_error"],
                now_iso(),
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
    at = now_iso()
    try:
        tasks = create_client(account["session_cookie"]).list_today()
        status, error = "VALID", None
        result = {"valid": True, "task_count": len(tasks)}
    except Exception as exc:
        status, error = "INVALID", _safe_error(exc)
        result = {"valid": False, "error": error}
    with connect() as db:
        db.execute(
            "UPDATE campus_accounts SET session_status=?,last_checked_at=?,last_error=?,updated_at=? WHERE id=?",
            (status, at, error, at, account_id),
        )
    return result


def _validate(name, session_cookie):
    name = str(name or "").strip()
    session_cookie = str(session_cookie or "").strip()
    if not name or len(name) > 80:
        raise ValueError("账号名称必须为 1–80 个字符")
    if not session_cookie or "=" not in session_cookie or len(session_cookie) > 12_000:
        raise ValueError("Cookie 格式无效")
    if "\r" in session_cookie or "\n" in session_cookie:
        raise ValueError("Cookie 不得包含换行符")
    return name, session_cookie


def _safe_error(exc):
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in ("cookie", "token", "authorization", "session=")):
        return "认证信息无效或已失效"
    return text[:300]
