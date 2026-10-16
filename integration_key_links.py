"""Partner API keys and operator-created, single-use delivery links in MSSQL."""
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

import pyodbc
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from database import sql_connection

router = APIRouter(include_in_schema=False)
NO_CACHE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
UNAVAILABLE = "Link nije važeći, istekao je ili je već iskorišćen."


def _key_secret() -> bytes:
    secret = os.getenv("INTEGRATION_KEY_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Preuzimanje ključa nije podešeno.", headers=NO_CACHE)
    return secret.encode()


def _partner_key(key_id: str) -> str:
    # A server-only secret lets us deliver a key again without storing plaintext keys.
    digest = hmac.new(_key_secret(), b"smartherz-partner-key-v1:" + key_id.encode(), hashlib.sha256).digest()
    return "smh_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pin_digest(token: str, pin: str) -> str:
    return hmac.new(token.encode(), pin.encode(), hashlib.sha256).hexdigest()


def _validate_partner(partner: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", partner):
        raise ValueError("Partner mora imati 1–100 malih slova, cifara, crtica ili donjih crta.")


def require_partner_key(api_key: str | None) -> None:
    if not api_key or not re.fullmatch(r"smh_[A-Za-z0-9_-]{43}", api_key):
        raise HTTPException(401, "Invalid integration API key.")
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            row = connection.cursor().execute("""SELECT 1 FROM devplay.SmartHerzPartners
                WHERE key_digest = ? AND revoked_at IS NULL""", _digest(api_key)).fetchone()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Integration key verification is unavailable.") from None
    if not row:
        raise HTTPException(401, "Invalid integration API key.")


def create_partner(partner: str) -> None:
    """Create a key, or replace a revoked key. Active partners use create_link instead."""
    _validate_partner(partner)
    key_id = secrets.token_urlsafe(32)
    key_digest = _digest(_partner_key(key_id))
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            row = cursor.execute("""SELECT revoked_at FROM devplay.SmartHerzPartners WITH (UPDLOCK, HOLDLOCK)
                WHERE partner_id = ?""", partner).fetchone()
            if row and row[0] is None:
                raise ValueError("Partner već ima aktivan ključ. Za novi link koristite komandu link.")
            if row:
                cursor.execute("""UPDATE devplay.SmartHerzPartners
                    SET key_id = ?, key_digest = ?, created_at = SYSUTCDATETIME(), revoked_at = NULL
                    WHERE partner_id = ?""", key_id, key_digest, partner)
            else:
                cursor.execute("""INSERT INTO devplay.SmartHerzPartners (partner_id, key_id, key_digest)
                    VALUES (?, ?, ?)""", partner, key_id, key_digest)
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Kreiranje partnera nije dostupno.", headers=NO_CACHE) from None


def revoke_partner(partner: str) -> None:
    _validate_partner(partner)
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            cursor.execute("""UPDATE devplay.SmartHerzPartners
                SET revoked_at = COALESCE(revoked_at, SYSUTCDATETIME()) WHERE partner_id = ?""", partner)
            if not cursor.rowcount:
                raise ValueError("Partner ne postoji.")
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Ukidanje pristupa nije dostupno.", headers=NO_CACHE) from None


def create_link(base_url: str, partner: str, expires_hours: int = 168) -> dict:
    """Run by an operator in the backend environment; never exposed as a public API."""
    url = urlsplit(base_url)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})) or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("Base URL mora biti HTTPS, odnosno HTTP za localhost, bez putanje.")
    if expires_hours not in {1, 24, 168}:
        raise ValueError("Rok važenja mora biti 1, 24 ili 168 sati.")
    _validate_partner(partner)
    _key_secret()
    token, pin = secrets.token_urlsafe(32), f"{secrets.randbelow(10000):04d}"
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            cursor.execute("DELETE FROM devplay.SmartHerzKeyLinks WHERE expires_at <= SYSUTCDATETIME()")
            row = cursor.execute("""SELECT key_id, key_digest FROM devplay.SmartHerzPartners WITH (HOLDLOCK)
                WHERE partner_id = ? AND revoked_at IS NULL""", partner).fetchone()
            if not row:
                raise ValueError("Partner ne postoji ili mu je pristup ukinut.")
            key = _partner_key(row[0])
            if not hmac.compare_digest(row[1], _digest(key)):
                raise HTTPException(503, "Tajna za predaju ključeva ne odgovara partneru.", headers=NO_CACHE)
            cursor.execute("""INSERT INTO devplay.SmartHerzKeyLinks
                (token_digest, pin_digest, key_digest, expires_at)
                OUTPUT INSERTED.expires_at
                VALUES (?, ?, ?, DATEADD(hour, ?, SYSUTCDATETIME()))""",
                _digest(token), _pin_digest(token, pin), _digest(key), expires_hours)
            expires_at = cursor.fetchone()[0]
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Kreiranje linka nije dostupno.", headers=NO_CACHE) from None
    return {"partner": partner, "url": base_url.rstrip("/") + "/integration/key#" + token, "pin": pin,
            "expires_at": expires_at.replace(tzinfo=timezone.utc).isoformat()}


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
    # Check configuration before touching a single-use link.
    _key_secret()
    token_digest = _digest(payload.token)
    status, message = 410, UNAVAILABLE
    try:
        with sql_connection() as connection:
            connection.timeout = 15
            cursor = connection.cursor()
            # The update lock lasts until commit, including across Azure replicas.
            cursor.execute("""SELECT pin_digest, key_digest,
                CASE WHEN expires_at <= SYSUTCDATETIME() THEN 1 ELSE 0 END, attempts
                FROM devplay.SmartHerzKeyLinks WITH (UPDLOCK, HOLDLOCK)
                WHERE token_digest = ?""", token_digest)
            row = cursor.fetchone()
            if row:
                pin_digest, key_digest, expired, attempts = row
                partner = cursor.execute("""SELECT key_id FROM devplay.SmartHerzPartners WITH (HOLDLOCK)
                    WHERE key_digest = ? AND revoked_at IS NULL""", key_digest).fetchone()
                if expired or not partner:
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
            # Commit before replying, including failed PIN attempts and consumed links.
            connection.commit()
    except (pyodbc.Error, RuntimeError):
        raise HTTPException(503, "Preuzimanje ključa nije dostupno.", headers=NO_CACHE) from None
    if status != 200:
        raise HTTPException(status, message, headers=NO_CACHE)
    return JSONResponse({"api_key": key}, headers=NO_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Partnerski API ključevi i jednokratni linkovi.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ["create", "link", "revoke"]:
        sub = commands.add_parser(command)
        sub.add_argument("--partner", required=True, help="Na primer smart-era")
        if command != "revoke":
            sub.add_argument("--base-url", required=True)
            sub.add_argument("--expires-hours", type=int, choices=[1, 24, 168], default=168)
    args = parser.parse_args()
    try:
        if args.command == "revoke":
            revoke_partner(args.partner)
            result = {"partner": args.partner, "revoked": True}
        else:
            if args.command == "create":
                create_partner(args.partner)
            result = create_link(args.base_url, args.partner, args.expires_hours)
        print(json.dumps(result))
    except (ValueError, HTTPException) as error:
        parser.error(error.detail if isinstance(error, HTTPException) else str(error))
