"""Validate a clinic's config and every key it needs.

    python -m core.check_env                 # format checks only, no network
    python -m core.check_env --live          # also call each vendor to confirm the key works
    CLINIC=other_clinic python -m core.check_env

Exit code is 0 only if everything requested passes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time

import httpx

from core.config import ConfigError, ClinicConfig, load_clinic_config
from core.settings import ALL_GROUPS, Settings, load_settings, problems

TIMEOUT = httpx.Timeout(10.0)


def _secret(s) -> str:
    return s.get_secret_value() if s is not None else ""


def _livekit_token(key: str, secret: str) -> str:
    """Minimal HS256 JWT with a roomList grant, enough to call ListRooms."""
    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iss": key, "nbf": now - 10, "exp": now + 60, "video": {"roomList": True}}).encode())
    sig = hmac.new(secret.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    return f"{header}.{claims}.{b64(sig)}"


def live_checks(s: Settings, clinic: ClinicConfig) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def run(name: str, fn) -> None:
        try:
            r = fn()
            ok = r.status_code < 300
            results.append((name, ok, f"HTTP {r.status_code}" + ("" if ok else f": {r.text[:160]}")))
        except httpx.HTTPError as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))

    with httpx.Client(timeout=TIMEOUT) as c:
        if s.livekit_url and s.livekit_api_key and s.livekit_api_secret:
            base = s.livekit_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
            token = _livekit_token(s.livekit_api_key, _secret(s.livekit_api_secret))
            run("LiveKit", lambda: c.post(f"{base}/twirp/livekit.RoomService/ListRooms",
                                          headers={"Authorization": f"Bearer {token}"}, json={}))
        if s.deepgram_api_key:
            run("Deepgram", lambda: c.get("https://api.deepgram.com/v1/projects",
                                          headers={"Authorization": f"Token {_secret(s.deepgram_api_key)}"}))
        if s.cartesia_api_key:
            headers = {"X-API-Key": _secret(s.cartesia_api_key), "Cartesia-Version": "2025-04-16"}
            if clinic.voice.voice_id:
                run("Cartesia voice", lambda: c.get(f"https://api.cartesia.ai/voices/{clinic.voice.voice_id}", headers=headers))
            else:
                run("Cartesia", lambda: c.get("https://api.cartesia.ai/voices", headers=headers))
        if s.llm_api_key and s.llm_base_url and s.llm_model:
            # A 1-token completion proves the key, the base URL and the model name together.
            run(f"LLM ({s.llm_model})", lambda: c.post(
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {_secret(s.llm_api_key)}"},
                json={"model": s.llm_model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
            ))
        if s.twilio_account_sid and s.twilio_auth_token:
            run("Twilio", lambda: c.get(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}.json",
                                        auth=(s.twilio_account_sid, _secret(s.twilio_auth_token))))
        if s.notify_channel == "sendgrid" and s.sendgrid_api_key:
            run("SendGrid", lambda: c.get("https://api.sendgrid.com/v3/scopes",
                                          headers={"Authorization": f"Bearer {_secret(s.sendgrid_api_key)}"}))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="call each vendor API to confirm keys work")
    parser.add_argument("--groups", nargs="*", choices=ALL_GROUPS, default=list(ALL_GROUPS))
    args = parser.parse_args(argv)

    settings = load_settings()
    print(f"Clinic: {settings.clinic}  ({settings.clinic_dir})")
    failed = False

    try:
        clinic = load_clinic_config(settings.clinic_dir)
        print(f"  [ok]   clinic.yaml valid: {clinic.name}")
    except ConfigError as e:
        print(f"  [FAIL] {e}")
        return 1

    if "agent" in args.groups and not clinic.voice.voice_id:
        print("  [FAIL] voice.voice_id is not set in clinic.yaml (agent)")
        failed = True

    for group in args.groups:
        found = problems(settings, [group])
        if found:
            failed = True
            for p in found:
                print(f"  [FAIL] {p}")
        else:
            print(f"  [ok]   {group} keys present and well-formed")

    if args.live:
        print("Live checks:")
        results = live_checks(settings, clinic)
        if not results:
            print("  (no keys to check)")
        for name, ok, detail in results:
            print(f"  [{'ok' if ok else 'FAIL':<4}] {name}: {detail}")
            failed |= not ok

    print("All checks passed." if not failed else "Some checks failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
