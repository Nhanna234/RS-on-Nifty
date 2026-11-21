"""
Sign-in for the website: usernames and passwords kept in the app's Secrets (no database).

No Streamlit imports, so it can be tested on its own. app.py draws the sign-in page.

Where the accounts live
-----------------------
In Streamlit Cloud: the app's Settings > Secrets box (locally: .streamlit/secrets.toml, never committed).

    [login]
    cookie_secret = "a long random text"          # signs the "keep me signed in" cookie
    admins = ["ali"]                              # may use the account tools in the app

    [login.users]
    ali = "pbkdf2_sha256$600000$<salt>$<hash>"    # one line per person
    ravi = "pbkdf2_sha256$600000$<salt>$<hash>"

Passwords are never stored, only a scrambled form (PBKDF2-SHA256 with a random salt) that cannot be
turned back into the password. The app's account tools make these lines for you.

If no accounts are set up, the website stays locked and only shows the setup page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
import secrets
import time

HASH_ITERATIONS = 600_000      # PBKDF2-SHA256 rounds (OWASP's recommendation); slows down password guessing
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_ATTEMPTS = 5        # wrong passwords in a row before a username is locked
LOCK_MINUTES = 15              # how long it stays locked
REMEMBER_DAYS = 7              # "keep me signed in on this device"
COOKIE_NAME = "rs_session"
USERNAME_RULE = re.compile(r"^[a-z0-9_-]{2,32}$")
USERNAME_HELP = "2 to 32 characters: small letters, digits, - or _"


# ──────────────────────────────────────────────────────────── passwords
def hash_password(password: str, iterations: int = HASH_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, confirm: str | None = None) -> str | None:
    """A message if the password is not acceptable, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if confirm is not None and password != confirm:
        return "The two passwords are not the same."
    return None


def clean_username(name: str) -> str:
    return str(name or "").strip().lower()


# ──────────────────────────────────────────────────────────── configuration
def load_config(secrets_obj) -> dict:
    """Reads the [login] section of the Secrets. Returns {"users": {name: hash}, "admins": set, "secret": str}."""
    try:
        section = secrets_obj.get("login", {}) if secrets_obj is not None else {}
    except Exception:  # noqa: BLE001  (no secrets file at all)
        section = {}
    users = {clean_username(k): str(v) for k, v in dict(section.get("users", {}) or {}).items()}
    admins = {clean_username(a) for a in (section.get("admins", []) or [])}
    return {"users": users, "admins": admins & set(users), "secret": str(section.get("cookie_secret", "") or "")}


def secrets_block(username: str, password_hash: str) -> str:
    """The complete [login] section for the first administrator, ready to paste into Secrets."""
    return (f'[login]\ncookie_secret = "{secrets.token_hex(32)}"\nadmins = ["{username}"]\n\n'
            f'[login.users]\n{username} = "{password_hash}"\n')


def user_line(username: str, password_hash: str) -> str:
    """One line to add under [login.users]."""
    return f'{username} = "{password_hash}"'


# ──────────────────────────────────────────────────────────── wrong-password lock
class AttemptLog:
    """Counts wrong passwords per username for everyone using the app; locks a username for LOCK_MINUTES after
    MAX_FAILED_ATTEMPTS in a row. It is kept in memory, so a restart of the app clears it."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.failed: dict[str, int] = {}
        self.locked_until: dict[str, float] = {}

    def minutes_locked(self, username: str) -> int:
        left = self.locked_until.get(username, 0) - self.clock()
        return math.ceil(left / 60) if left > 0 else 0

    def failure(self, username: str) -> None:
        n = self.failed.get(username, 0) + 1
        if n >= MAX_FAILED_ATTEMPTS:
            self.locked_until[username] = self.clock() + LOCK_MINUTES * 60
            n = 0
        self.failed[username] = n

    def success(self, username: str) -> None:
        self.failed.pop(username, None)
        self.locked_until.pop(username, None)


def check_login(cfg: dict, attempts: AttemptLog, username: str, password: str) -> tuple[bool, str]:
    """(signed in?, message). The message never says whether the username exists."""
    username = clean_username(username)
    locked = attempts.minutes_locked(username)
    if locked:
        return False, f"Too many wrong passwords. Try again in {locked} minute{'s' if locked != 1 else ''}."
    stored = cfg["users"].get(username)
    if stored is None:
        verify_password(password, hash_password("x", 1000))   # similar effort, so timing does not reveal names
        attempts.failure(username)
        return False, "Wrong username or password."
    if not verify_password(password, stored):
        attempts.failure(username)
        return False, "Wrong username or password."
    attempts.success(username)
    return True, ""


# ──────────────────────────────────────────────────────────── "keep me signed in" cookie
def _sign(secret: str, username: str, expires: int, stored_hash: str) -> str:
    # the password hash is part of the signature, so changing a password signs that person out everywhere
    msg = f"{username}|{expires}|{stored_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def make_token(cfg: dict, username: str, now: float | None = None, days: int = REMEMBER_DAYS) -> str | None:
    if not cfg["secret"] or username not in cfg["users"]:
        return None
    expires = int((now or time.time()) + days * 86400)
    raw = f"{username}|{expires}|{_sign(cfg['secret'], username, expires, cfg['users'][username])}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def read_token(cfg: dict, token: str | None, now: float | None = None) -> str | None:
    """The username the cookie belongs to, or None if it is missing, expired, tampered with, or the person's
    account was removed or their password changed."""
    if not token or not cfg["secret"]:
        return None
    try:
        username, expires, sig = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8").split("|")
        expires = int(expires)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if expires < (now or time.time()) or username not in cfg["users"]:
        return None
    good = _sign(cfg["secret"], username, expires, cfg["users"][username])
    return username if hmac.compare_digest(good, sig) else None
