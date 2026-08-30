from __future__ import annotations

import os
import subprocess

KEYCHAIN_SERVICE = "ai-digest-twitterapi-io"
KEYCHAIN_ACCOUNT = "api_key"


class TwitterApiIOKeyStore:
    def load(self) -> str | None:
        return os.environ.get("TWITTERAPI_IO_KEY") or _keychain_get()

    def save(self, value: str) -> None:
        key = value.strip()
        if not key:
            raise ValueError("TwitterAPI.io API key must not be empty")
        process = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
                key,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"failed to save TwitterAPI.io key in Keychain: {process.stderr.strip()}"
            )


def _keychain_get() -> str | None:
    try:
        process = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return process.stdout.strip() if process.returncode == 0 else None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
