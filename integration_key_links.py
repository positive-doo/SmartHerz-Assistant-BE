"""Operator-created, single-use delivery links for the configured integration key."""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(include_in_schema=False)
NO_CACHE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
UNAVAILABLE = "Link nije važeći, istekao je ili je već iskorišćen."


def _settings() -> tuple[Path, str]:
    database = os.getenv("INTEGRATION_KEY_LINKS_DB", "").strip()
    key = os.getenv("INTEGRATION_API_KEY", "")
    if not database or not key.strip():
        raise HTTPException(503, "Preuzimanje ključa nije podešeno.", headers=NO_CACHE)
    return Path(database).resolve(), key


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pin_digest(token: str, pin: str) -> str:
    return hmac.new(token.encode(), pin.encode(), hashlib.sha256).hexdigest()


def create_link(base_url: str, expires_hours: int = 168) -> dict:
    """Run by an operator in the backend environment; never exposed as a public API."""
    url = urlsplit(base_url)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})) or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("Base URL mora biti HTTPS, odnosno HTTP za localhost, bez putanje.")
    if expires_hours not in {1, 24, 168}:
        raise ValueError("Rok važenja mora biti 1, 24 ili 168 sati.")
    database, key = _settings()
    database.parent.mkdir(parents=True, exist_ok=True)
    token, pin = secrets.token_urlsafe(32), f"{secrets.randbelow(10000):04d}"
    now = int(time.time())
    expires_at = now + expires_hours * 3600
    with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS key_links (
            token_digest TEXT PRIMARY KEY, pin_digest TEXT NOT NULL,
            key_digest TEXT NOT NULL, expires_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
        )""")
        connection.execute("DELETE FROM key_links WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO key_links (token_digest, pin_digest, key_digest, expires_at) VALUES (?, ?, ?, ?)",
                           (_digest(token), _pin_digest(token, pin), _digest(key), expires_at))
    return {"url": base_url.rstrip("/") + "/integration/key#" + token, "pin": pin,
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat()}


class RedeemRequest(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    pin: str = Field(pattern=r"^[0-9]{4}$")


@router.get("/integration/key", response_class=HTMLResponse)
def key_page():
    _settings()
    nonce = secrets.token_urlsafe(16)
    page = (Path(__file__).parent / "templates" / "integration-key.html").read_text(encoding="utf-8")
    headers = {**NO_CACHE, "Content-Security-Policy":
               f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}
    return HTMLResponse(page.replace("__NONCE__", nonce), headers=headers)


@router.post("/api/v1/integration/key-links/redeem")
def redeem_link(payload: RedeemRequest):
    database, key = _settings()
    if not database.is_file():
        raise HTTPException(503, "Preuzimanje ključa nije dostupno.", headers=NO_CACHE)
    token_digest = _digest(payload.token)
    status, message = 410, UNAVAILABLE
    try:
        with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
            # Serialize checking and deletion across requests/processes using this file.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT pin_digest, key_digest, expires_at, attempts FROM key_links WHERE token_digest = ?", (token_digest,)).fetchone()
            if row:
                pin_digest, key_digest, expires_at, attempts = row
                if expires_at <= time.time() or not hmac.compare_digest(key_digest, _digest(key)):
                    connection.execute("DELETE FROM key_links WHERE token_digest = ?", (token_digest,))
                elif not hmac.compare_digest(pin_digest, _pin_digest(payload.token, payload.pin)):
                    if attempts + 1 >= 5:
                        connection.execute("DELETE FROM key_links WHERE token_digest = ?", (token_digest,))
                    else:
                        connection.execute("UPDATE key_links SET attempts = attempts + 1 WHERE token_digest = ?", (token_digest,))
                        status, message = 403, "PIN nije ispravan."
                else:
                    connection.execute("DELETE FROM key_links WHERE token_digest = ?", (token_digest,))
                    status = 200
        # Commit before replying, including failed PIN attempts and consumed links.
    except sqlite3.Error:
        raise HTTPException(503, "Preuzimanje ključa nije dostupno.", headers=NO_CACHE) from None
    if status != 200:
        raise HTTPException(status, message, headers=NO_CACHE)
    return JSONResponse({"api_key": key}, headers=NO_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kreiranje jednokratnog linka za postojeći SMART ERA API ključ.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expires-hours", type=int, choices=[1, 24, 168], default=168)
    args = parser.parse_args()
    try:
        print(json.dumps(create_link(args.base_url, args.expires_hours)))
    except (ValueError, HTTPException, OSError, sqlite3.Error) as error:
        parser.error(error.detail if isinstance(error, HTTPException) else str(error))
