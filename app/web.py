"""Shared web plumbing: templates, auth guards, form helpers."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings

BASE_DIR = Path(__file__).resolve().parent

# Bump when static assets change to bust browser caches.
ASSET_VERSION = "1"

AVATARS = ["🦉", "🦊", "🐻", "🐢", "🦁", "🐬", "🦄", "🐝", "🐙", "🦖", "🐧", "🐰"]
COLORS = ["sky", "rose", "amber", "emerald", "violet", "orange"]

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(v=ASSET_VERSION, avatars=AVATARS, colors=COLORS)


def fmt_date(d: date | None) -> str:
    if d is None:
        return ""
    return f"{d:%a %b} {d.day}"


def due_class(d: date | None) -> str:
    from app.services.stats import today

    if d is None:
        return "text-ink-soft"
    delta = (d - today()).days
    if delta < 0:
        return "text-rose-700 font-semibold"
    if delta <= 2:
        return "text-amber-700 font-semibold"
    return "text-ink-soft"


def scope_label(scope: str) -> str:
    """Attempt scope code → words for the parent's attempt list."""
    kind, _, rest = scope.partition(":")
    if kind == "partial":
        return "Line 1" if rest == "1" else f"Lines 1–{rest}"
    if kind == "drill":
        sub, _, n = rest.partition(":")
        return f"Drill: line {n}" if sub == "line" else f"Drill: lines {int(n) - 1}→{n}"
    return {"full": "Full run", "review": "Review"}.get(kind, scope)


def local_time(ts: datetime) -> str:
    if ts.tzinfo is None:  # SQLite hands back naive UTC
        ts = ts.replace(tzinfo=UTC)
    t = ts.astimezone(ZoneInfo(get_settings().timezone))
    return f"{t:%b} {t.day}, {t:%-I:%M %p}"


templates.env.filters.update(
    fdate=fmt_date, due_class=due_class, scope_label=scope_label, localtime=local_time
)


class LoginRequired(Exception):
    """Raised by `require_parent`; handled in main.py with a redirect to the login page."""


class UnlockRequired(Exception):
    """Raised by `require_household` when HOUSEHOLD_PASSWORD is set and this device isn't unlocked."""


PARENT_SESSION_S = 12 * 3600


def is_parent(request: Request) -> bool:
    """Parent login lasts 12 hours; the cookie itself is long-lived for the household unlock."""
    since = request.session.get("parent")
    return isinstance(since, int | float) and time.time() - since < PARENT_SESSION_S


def require_parent(request: Request) -> None:
    if not is_parent(request):
        raise LoginRequired()


def require_household(request: Request) -> None:
    if get_settings().household_password and not (
        request.session.get("household") or is_parent(request)
    ):
        raise UnlockRequired()


def check_password(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class LoginThrottle:
    """Per-address lockout for the two password forms.

    Both passwords are short, human-chosen strings behind a public URL, so after
    `max_failures` wrong guesses inside `window_s` an address is refused until the
    oldest failure ages out. In-memory: the app runs as one worker for one household.
    Behind Traefik, uvicorn's --proxy-headers makes `request.client` the real address.
    """

    def __init__(self, max_failures: int = 5, window_s: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self.window_s:
            q.popleft()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._failures.get(key)
            if q is None:
                return False
            self._prune(q, now)
            if not q:
                del self._failures[key]
                return False
            return len(q) >= self.max_failures

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

    def record(self, key: str, ok: bool) -> None:
        now = time.monotonic()
        with self._lock:
            if ok:
                self._failures.pop(key, None)
                return
            q = self._failures[key]
            self._prune(q, now)
            q.append(now)


login_throttle = LoginThrottle()
TOO_MANY_TRIES = "Too many tries. Wait a few minutes, then try again."


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def parse_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_percent(raw: str | None, default: float = 1.0) -> float:
    try:
        v = float((raw or "").strip().rstrip("%"))
    except ValueError:
        return default
    return max(0.0, min(1.0, v / 100 if v > 1 else v))


def render(
    request: Request, name: str, ctx: dict[str, Any] | None = None, status: int = 200
) -> Any:
    context = {"is_parent": is_parent(request), "settings": get_settings()}
    context.update(ctx or {})
    return templates.TemplateResponse(request, name, context, status_code=status)
