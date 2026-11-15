"""One-tap sign-in to the teachers' web cabinet (webapp/cabinet.py): the bot's
"Web-kabinet" button carries a token signed with the web session secret that
names the Telegram user and expires after LOGIN_TTL seconds. Since 2026-11-15
it works once (its random `jti` is spent in Redis by webapp/cabinet.py) and for
5 minutes: a forwarded or logged link no longer opens the cabinet again."""
from __future__ import annotations

import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer

from bot.config import settings

LOGIN_TTL = 300
_SALT = "cabinet-login"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.web_session_secret, salt=_SALT)


def cabinet_login_url(user_id: int) -> str:
    token = _serializer().dumps({"uid": user_id, "jti": secrets.token_urlsafe(12)})
    return f"{settings.cabinet_base_url.rstrip('/')}/login?token={token}"


def read_login_token(token: str) -> tuple[int, str] | None:
    """(the user id, the token's one-time id) of a valid, unexpired token, else None."""
    try:
        data = _serializer().loads(token, max_age=LOGIN_TTL)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    uid, jti = data.get("uid"), data.get("jti")
    return (uid, jti) if isinstance(uid, int) and isinstance(jti, str) and jti else None
