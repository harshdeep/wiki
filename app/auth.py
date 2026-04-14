"""Single-password session cookie auth.

One password in `WIKI_PASSWORD`, one signing secret in `SECRET_KEY`. Logged-in
users carry a signed cookie; everyone else is bounced to `/login`.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Final

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger(__name__)

COOKIE_NAME: Final[str] = "wiki_session"
MAX_AGE_SECONDS: Final[int] = 60 * 60 * 24 * 30  # 30 days


def _secret() -> str:
    s = os.environ.get("SECRET_KEY")
    if not s:
        raise RuntimeError("SECRET_KEY env var is required")
    return s


def _password() -> str:
    p = os.environ.get("WIKI_PASSWORD")
    if not p:
        raise RuntimeError("WIKI_PASSWORD env var is required")
    return p


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="wiki-session-v1")


def check_password(submitted: str) -> bool:
    return hmac.compare_digest(submitted.encode(), _password().encode())


def issue_token() -> str:
    return _serializer().dumps({"ok": True})


def verify_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=MAX_AGE_SECONDS)
        return True
    except SignatureExpired:
        return False
    except BadSignature:
        return False
    except Exception:
        log.exception("unexpected error verifying session token")
        return False


def is_authed(request: Request) -> bool:
    return verify_token(request.cookies.get(COOKIE_NAME))


def redirect_to_login(next_url: str | None = None) -> RedirectResponse:
    dest = "/login"
    if next_url and next_url != "/login":
        from urllib.parse import quote

        dest = f"/login?next={quote(next_url)}"
    return RedirectResponse(dest, status_code=303)
