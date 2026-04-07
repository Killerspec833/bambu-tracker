#!/usr/bin/env python3
"""
Fetch a Bambu Lab cloud token and save it to ~/.bambu_token.

Usage:
    python3 get_token.py
    python3 get_token.py --email you@example.com
    python3 get_token.py --out /custom/path/token

The token is required for cloud MQTT (connecting via bambulab.com brokers).
It expires periodically — re-run this script when MQTT auth alerts appear.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

try:
    import urllib.request as _req
    import urllib.error as _err
except ImportError:
    print("ERROR: urllib not available", file=sys.stderr)
    sys.exit(1)


_LOGIN_URL = "https://bambulab.com/api/sign-in/form"
_DEFAULT_OUT = Path.home() / ".bambu_token"


def fetch_token(email: str, password: str) -> str:
    """POST to Bambu login endpoint and return the JWT token string."""
    payload = json.dumps({"account": email, "password": password}).encode()
    request = _req.Request(
        _LOGIN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "BambuTracker/1.0",
        },
        method="POST",
    )
    try:
        with _req.urlopen(request, timeout=15) as resp:
            body = resp.read().decode()
    except _err.HTTPError as e:
        body = e.read().decode()
        try:
            data = json.loads(body)
            msg = data.get("message") or data.get("error") or body
        except Exception:
            msg = body
        print(f"ERROR: Login failed ({e.code}): {msg}", file=sys.stderr)
        if e.code == 401:
            print("       Check your email and password.", file=sys.stderr)
        elif e.code == 400:
            print("       If you use MFA, approve the login prompt in the Bambu app first, then re-run.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Network error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"ERROR: Unexpected response: {body[:200]}", file=sys.stderr)
        sys.exit(1)

    # Bambu returns the token in different keys depending on the endpoint version
    token = (
        data.get("token")
        or data.get("accessToken")
        or data.get("access_token")
        or (data.get("data") or {}).get("token")
        or (data.get("data") or {}).get("accessToken")
    )

    if not token:
        print(f"ERROR: Token not found in response. Full response:\n{json.dumps(data, indent=2)}", file=sys.stderr)
        print("\nIf Bambu changed their API, check the response above and file an issue.", file=sys.stderr)
        sys.exit(1)

    return token


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Bambu Lab cloud token")
    parser.add_argument("--email", help="Bambu Lab account email")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT,
                        help=f"Where to save the token (default: {_DEFAULT_OUT})")
    args = parser.parse_args()

    print("Bambu Lab Token Fetcher")
    print("─" * 40)

    email = args.email or input("Bambu Lab email: ").strip()
    if not email:
        print("ERROR: Email is required.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if not password:
        print("ERROR: Password is required.", file=sys.stderr)
        sys.exit(1)

    print("Logging in…", end=" ", flush=True)
    token = fetch_token(email, password)
    print("OK")

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(token + "\n")
    out_path.chmod(0o600)  # owner read/write only

    print(f"Token saved to: {out_path}")
    print(f"Token length:   {len(token)} chars")
    print()
    print("Next steps:")
    print(f"  1. Set token_file in config.yaml:  token_file: \"{out_path}\"")
    print(f"  2. Set in .env:  CONFIG_PATH=/path/to/your/config.yaml")
    print(f"  3. Restart:  docker compose down && docker compose up -d")


if __name__ == "__main__":
    main()
