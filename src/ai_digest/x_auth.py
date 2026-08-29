from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import subprocess
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass

import httpx

KEYCHAIN_SERVICE = "ai-digest-x"


@dataclass
class XTokens:
    access_token: str
    refresh_token: str | None = None


class XTokenStore:
    def __init__(self, client_id: str | None = None):
        self.client_id = client_id or os.environ.get("AI_DIGEST_X_CLIENT_ID", "")

    def load(self) -> XTokens | None:
        access = os.environ.get("AI_DIGEST_X_ACCESS_TOKEN") or _keychain_get("access_token")
        refresh = os.environ.get("AI_DIGEST_X_REFRESH_TOKEN") or _keychain_get("refresh_token")
        return XTokens(access, refresh) if access else None

    def save(self, tokens: XTokens) -> None:
        _keychain_set("access_token", tokens.access_token)
        if tokens.refresh_token:
            _keychain_set("refresh_token", tokens.refresh_token)

    async def refresh(self, refresh_token: str) -> XTokens:
        if not self.client_id:
            raise RuntimeError("AI_DIGEST_X_CLIENT_ID is required for token refresh")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.x.com/2/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            payload = response.json()
        tokens = XTokens(payload["access_token"], payload.get("refresh_token") or refresh_token)
        self.save(tokens)
        return tokens


def authorize_pkce(
    client_id: str,
    redirect_uri: str = "http://127.0.0.1:8765/callback",
    timeout_seconds: int = 300,
) -> XTokens:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = secrets.token_urlsafe(24)
    scopes = "tweet.read users.read list.read list.write offline.access"
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    result: dict[str, str] = {}
    event = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            values = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({key: rows[0] for key, rows in values.items() if rows})
            body = b"X authorization received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            event.set()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    parsed = urllib.parse.urlparse(redirect_uri)
    server = http.server.ThreadingHTTPServer(
        (parsed.hostname or "127.0.0.1", parsed.port or 8765), Handler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(f"https://twitter.com/i/oauth2/authorize?{query}")
    if not event.wait(timeout_seconds):
        server.shutdown()
        raise TimeoutError("X OAuth callback was not received")
    server.shutdown()
    if result.get("state") != state or not result.get("code"):
        raise RuntimeError(f"invalid X OAuth callback: {result.get('error', 'missing code')}")
    response = httpx.post(
        "https://api.x.com/2/oauth2/token",
        data={
            "code": result["code"],
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    tokens = XTokens(payload["access_token"], payload.get("refresh_token"))
    XTokenStore(client_id).save(tokens)
    return tokens


def _keychain_get(account: str) -> str | None:
    try:
        process = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return process.stdout.strip() if process.returncode == 0 else None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _keychain_set(account: str, value: str) -> None:
    process = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            account,
            "-w",
            value,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if process.returncode != 0:
        raise RuntimeError(f"failed to save X token in Keychain: {process.stderr.strip()}")
