#!/usr/bin/env python
"""
Place a test call with the AI Krishi voice agent.

Usage:
    python call.py                              # call the default number
    python call.py +918618075133                # uses the saved language, or plays the menu
    python call.py +918618075133 --lang=hi      # call in Hindi (kn, hi or en)
    python call.py +918618075133 Rajesh --lang=en
    python call.py --status                     # show recent calls
    python call.py --check                      # preflight only, place no call

Requires the server and an ngrok tunnel to be running; use start_calling.py
to bring both up and sync BASE_URL automatically.
"""

import asyncio
import sys
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from app.config import get_settings, get_twilio_client, twilio_configured  # noqa: E402
from app.voice import call_log, speech  # noqa: E402
from app.voice.language import PROFILES, greeting_text, normalise_language  # noqa: E402

DEFAULT_TO = "+918618075133"


def preflight() -> bool:
    """Verify credentials, tunnel and webhook before spending a call."""
    settings = get_settings()
    ok = True

    if not twilio_configured():
        print("  [X] Twilio credentials missing (need API key or auth token)")
        return False
    print(f"  [OK] Twilio creds       {settings.TWILIO_ACCOUNT_SID}")

    if not settings.TWILIO_PHONE_NUMBER:
        print("  [X] TWILIO_PHONE_NUMBER not set")
        return False
    print(f"  [OK] From number        {settings.TWILIO_PHONE_NUMBER}")

    base = settings.BASE_URL.rstrip("/")
    if "localhost" in base or "127.0.0.1" in base:
        print(f"  [X] BASE_URL is local ({base}) - Twilio cannot reach it. Start ngrok.")
        return False

    try:
        req = urllib.request.Request(
            f"{base}/twilio/voice",
            data=b"CallSid=CApreflight&From=%2B10000000000",
            headers={"ngrok-skip-browser-warning": "1"},
        )
        body = urllib.request.urlopen(req, timeout=30).read().decode()
        if "<Response>" not in body:
            print(f"  [X] Webhook did not return TwiML: {body[:120]}")
            ok = False
        else:
            print(f"  [OK] Webhook reachable  {base}/twilio/voice")
    except Exception as e:
        print(f"  [X] Webhook unreachable: {e}")
        ok = False

    return ok


def show_status(limit: int = 10) -> None:
    client = get_twilio_client()
    print(f"{'created':20} {'dir':14} {'from':17} {'to':17} {'status':12} dur")
    print("-" * 90)
    for c in client.calls.list(limit=limit):
        created = str(c.date_created)[:19]
        print(
            f"{created:20} {str(c.direction):14} {str(c.from_formatted):17} "
            f"{str(c.to):17} {str(c.status):12} {c.duration}s"
        )


def place_call(to: str, name: str = "", language: str = "") -> int:
    settings = get_settings()
    base = settings.BASE_URL.rstrip("/")

    if not to.startswith("+"):
        to = "+91" + to.lstrip("0")

    if language:
        call_log.set_language_preference(to, language)
    saved = language or call_log.get_language_preference(to)
    spoken = PROFILES[saved].english_name if saved else "language menu"
    print(f"\nCalling {to} from {settings.TWILIO_PHONE_NUMBER} ({spoken}) ...")

    params = {}
    if name:
        params["name"] = name
    if language:
        params["lang"] = language
    voice_url = f"{base}/twilio/voice"
    if params:
        voice_url += "?" + urllib.parse.urlencode(params)

    if name and saved:
        # Generate the greeting BEFORE dialing. The file is content-addressed,
        # so the server reuses it instantly and the caller hears no dead air.
        print("  pre-generating greeting ...", end=" ", flush=True)
        made = asyncio.run(speech.synthesize(greeting_text(saved, name), saved, prefix="greet_"))
        print("done" if made else "failed")

    client = get_twilio_client()
    try:
        call = client.calls.create(
            to=to,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=voice_url,
            status_callback=f"{base}/twilio/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
    except Exception as e:
        msg = str(e)
        print(f"\nCall rejected by Twilio:\n  {msg[:300]}\n")
        if "21219" in msg or "not verified" in msg.lower():
            print("  Trial accounts can only call verified numbers.")
            print("  Add this number under Console > Phone Numbers > Verified Caller IDs.")
        elif "21606" in msg or "isn't assigned" in msg.lower():
            print("  The From number is not owned by this account.")
            print("  Check TWILIO_PHONE_NUMBER in .env against the Console.")
        return 1

    print(f"  call_sid: {call.sid}")

    last = None
    for _ in range(40):
        status = client.calls(call.sid).fetch()
        if status.status != last:
            print(f"  {status.status}")
            last = status.status
        if status.status in ("completed", "failed", "busy", "no-answer", "canceled"):
            print(f"\nFinished: {status.status}, {status.duration}s")
            return 0 if status.status == "completed" else 1
        time.sleep(3)

    print("\nStopped watching (call may still be active).")
    return 0


def main() -> int:
    args = list(sys.argv[1:])

    if "--status" in args:
        show_status()
        return 0

    check_only = "--check" in args
    language = ""
    for arg in args:
        if arg.startswith("--lang="):
            language = normalise_language(arg.split("=", 1)[1], default=None) or ""
            if not language:
                print("--lang must be kn, hi or en")
                return 2
    args = [a for a in args if not a.startswith("--")]

    print("Preflight:")
    if not preflight():
        print("\nPreflight failed - not placing a call.")
        return 1

    if check_only:
        print("\nPreflight passed (--check, no call placed).")
        return 0

    to = args[0] if args else DEFAULT_TO
    name = args[1] if len(args) > 1 else ""
    return place_call(to, name, language)


if __name__ == "__main__":
    raise SystemExit(main())
