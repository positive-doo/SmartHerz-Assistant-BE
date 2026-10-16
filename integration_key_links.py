"""AdminApp-managed API keys and PIN-protected single-use delivery in shared MSSQL."""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pyodbc
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from database import sql_connection

router = APIRouter(include_in_schema=False)
NO_CACHE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
UNAVAILABLE = "Link nije važeći, istekao je ili je već iskorišćen."
_ACTIVE_KEY_SQL = """k.app_id = 66 AND k.is_active = 1 AND k.revoked_at IS NULL
    AND (k.expires_at IS NULL OR k.expires_at > SYSUTCDATETIME())
    AND EXISTS (SELECT 1 FROM admin.AdminApiKeyScopeAssignments a
        INNER JOIN admin.AdminApiKeyScopes s ON s.scope_id = a.scope_id
        WHERE a.api_key_id = k.api_key_id AND s.scope = 'integration:read'
            AND s.is_active = 1 AND (s.app_id IS NULL OR s.app_id = 66))"""


def _key_secret() -> bytes:
    secret = os.getenv("INTEGRATION_KEY_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Preuzimanje ključa nije podešeno.", headers=NO_CACHE)
    return secret.encode()


def _partner_key(key_id: str) -> str:
    digest = hmac.new(_key_secret(), b"smartherz-partner-key-v1:" + key_id.encode(), hashlib.sha256).digest()
    return "smh_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pin_digest(token: str, pin: str) -> str:
    return hmac.new(token.encode(), pin.encode(), hashlib.sha256).hexdigest()


def require_partner_key(api_key: str | None) -> None:
    if not api_key or not re.fullmatch(r"(?:smh_|cc_)[A-Za-z0-9_-]{43}", api_key):
        raise HTTPException(401, "Invalid integration API key.")
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            row = connection.cursor().execute("SELECT 1 FROM admin.AdminApiKeys k WHERE k.key_hash = ? AND "
                                              + _ACTIVE_KEY_SQL, _digest(api_key)).fetchone()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Integration key verification is unavailable.") from None
    if not row:
        raise HTTPException(401, "Invalid integration API key.")


def create_link(base_url: str, api_key_id: str, expires_hours: int = 168) -> dict:
    """Optional operator command; keys are created, rotated and revoked in AdminApp."""
    url = urlsplit(base_url)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})) or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("Base URL mora biti HTTPS, odnosno HTTP za localhost, bez putanje.")
    if expires_hours not in {1, 24, 168}:
        raise ValueError("Rok važenja mora biti 1, 24 ili 168 sati.")
    api_key_id = str(UUID(api_key_id))
    _key_secret()
    token, pin = secrets.token_urlsafe(32), f"{secrets.randbelow(10000):04d}"
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            cursor.execute("DELETE FROM devplay.SmartHerzKeyLinks WHERE expires_at <= SYSUTCDATETIME()")
            row = cursor.execute("""SELECT p.key_id, k.key_hash,
                CASE WHEN k.expires_at < DATEADD(hour, ?, SYSUTCDATETIME())
                    THEN k.expires_at ELSE DATEADD(hour, ?, SYSUTCDATETIME()) END
                FROM admin.AdminApiKeys k WITH (HOLDLOCK)
                INNER JOIN devplay.SmartHerzPartners p WITH (HOLDLOCK) ON p.key_digest = k.key_hash
                WHERE k.api_key_id = ? AND """ + _ACTIVE_KEY_SQL,
                expires_hours, expires_hours, api_key_id).fetchone()
            if not row:
                raise ValueError("Ključ ne postoji, nema dozvolu za integraciju ili mu je pristup ukinut.")
            key = _partner_key(row[0])
            if not hmac.compare_digest(row[1], _digest(key)):
                raise HTTPException(503, "Tajna za predaju ključeva nije usklađena.", headers=NO_CACHE)
            cursor.execute("""INSERT INTO devplay.SmartHerzKeyLinks
                (token_digest, pin_digest, key_digest, expires_at) VALUES (?, ?, ?, ?)""",
                _digest(token), _pin_digest(token, pin), _digest(key), row[2])
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Kreiranje linka nije dostupno.", headers=NO_CACHE) from None
    return {"api_key_id": api_key_id, "url": base_url.rstrip("/") + "/integration/key#" + token, "pin": pin,
            "expires_at": row[2].replace(tzinfo=timezone.utc).isoformat()}


class RedeemRequest(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    pin: str = Field(pattern=r"^[0-9]{4}$")


@router.get("/integration/key", response_class=HTMLResponse)
def key_page():
    nonce = secrets.token_urlsafe(16)
    page = (Path(__file__).parent / "templates" / "integration-key.html").read_text(encoding="utf-8")
    headers = {**NO_CACHE, "Content-Security-Policy":
               f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}
    return HTMLResponse(page.replace("__NONCE__", nonce), headers=headers)


@router.post("/api/v1/integration/key-links/redeem")
def redeem_link(payload: RedeemRequest):
    _key_secret()
    token_digest = _digest(payload.token)
    status, message = 410, UNAVAILABLE
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            cursor.execute("""SELECT pin_digest, key_digest,
                CASE WHEN expires_at <= SYSUTCDATETIME() THEN 1 ELSE 0 END, attempts
                FROM devplay.SmartHerzKeyLinks WITH (UPDLOCK, HOLDLOCK)
                WHERE token_digest = ?""", token_digest)
            row = cursor.fetchone()
            if row:
                pin_digest, key_digest, expired, attempts = row
                partner = None if expired else cursor.execute("""SELECT p.key_id
                    FROM admin.AdminApiKeys k WITH (HOLDLOCK)
                    INNER JOIN devplay.SmartHerzPartners p WITH (HOLDLOCK) ON p.key_digest = k.key_hash
                    WHERE k.key_hash = ? AND """ + _ACTIVE_KEY_SQL, key_digest).fetchone()
                if not partner:
                    cursor.execute("DELETE FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?", token_digest)
                elif not hmac.compare_digest(pin_digest, _pin_digest(payload.token, payload.pin)):
                    if attempts + 1 >= 5:
                        cursor.execute("DELETE FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?", token_digest)
                    else:
                        cursor.execute("UPDATE devplay.SmartHerzKeyLinks SET attempts = attempts + 1 WHERE token_digest = ?", token_digest)
                        status, message = 403, "PIN nije ispravan."
                else:
                    key = _partner_key(partner[0])
                    if not hmac.compare_digest(key_digest, _digest(key)):
                        raise HTTPException(503, "Preuzimanje ključa nije dostupno.", headers=NO_CACHE)
                    cursor.execute("DELETE FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?", token_digest)
                    status = 200
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Preuzimanje ključa nije dostupno.", headers=NO_CACHE) from None
    if status != 200:
        raise HTTPException(status, message, headers=NO_CACHE)
    return JSONResponse({"api_key": key}, headers=NO_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novi jednokratni link za ključ kojim upravlja AdminApp.")
    parser.add_argument("--key-id", required=True, help="API key ID iz AdminApp-a")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expires-hours", type=int, choices=[1, 24, 168], default=168)
    args = parser.parse_args()
    try:
        print(json.dumps(create_link(args.base_url, args.key_id, args.expires_hours)))
    except (ValueError, HTTPException) as error:
        parser.error(error.detail if isinstance(error, HTTPException) else str(error))
