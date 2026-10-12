"""One-tap sign-in to the teachers' web cabinet (webapp/cabinet.py): the bot's
"Web-kabinet" button carries a token signed with the web session secret that
names the Telegram user and expires after LOGIN_TTL seconds."""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

from bot.config import settings

LOGIN_TTL = 600
_SALT = "cabinet-login"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.web_session_secret, salt=_SALT)


def cabinet_login_url(user_id: int) -> str:
    token = _serializer().dumps({"uid": user_id})
    return f"{settings.cabinet_base_url.rstrip('/')}/login?token={token}"


def read_login_token(token: str) -> int | None:
    """The user id of a valid, unexpired token, else None."""
    try:
        data = _serializer().loads(token, max_age=LOGIN_TTL)
    except BadSignature:
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return uid if isinstance(uid, int) else None
