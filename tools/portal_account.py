#!/usr/bin/env python
"""
Registry office tool for farmer portal accounts.

    python tools/portal_account.py create --mobile 8618075133 --verify
    python tools/portal_account.py reset --farmer-id KA-KLB-26-00001-7
    python tools/portal_account.py verify --farmer-id KA-KLB-26-00001-7 --status VERIFIED
    python tools/portal_account.py show --mobile 8618075133

`create` fills anything not given on the command line from the existing
voice-agent farmer record for that number, when there is one.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.portal import auth, service  # noqa: E402
from app.portal.service import PortalError  # noqa: E402


def _legacy_record(mobile: str) -> dict:
    normalised = auth.normalise_mobile(mobile)
    path = os.path.join("data", "krishi.sqlite3")
    if not normalised or not os.path.exists(path):
        return {}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM farmers WHERE phone = ?", (normalised,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def _state_name(value: str) -> str:
    for name in auth.STATE_CODES:
        if name.lower() == (value or "").strip().lower():
            return name
    return value or "Karnataka"


def _acres(value) -> str:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return match.group(0) if match else ""


def cmd_create(args) -> int:
    legacy = _legacy_record(args.mobile)
    data = {
        "full_name": args.name or legacy.get("name") or "",
        "mobile": args.mobile,
        "village": args.village or legacy.get("village") or legacy.get("city") or "",
        "taluk": args.taluk or legacy.get("taluk") or "",
        "district": args.district or legacy.get("district") or legacy.get("city") or "",
        "state": _state_name(args.state or legacy.get("state") or "Karnataka"),
        "pincode": args.pincode or "",
        "declared_land_acres": args.land or _acres(legacy.get("land_size")),
    }
    try:
        result = service.register_farmer(data, require_consent=False)
    except PortalError as exc:
        print(f"Could not create the account: {exc.message}")
        for field, message in exc.fields.items():
            print(f"  {field}: {message}")
        return 1

    farmer = result["farmer"]
    if args.verify:
        farmer = service.set_verification(farmer["farmer_id"], "VERIFIED", "Registry operator (CLI)")

    creds = result["credentials"]
    print("Farmer account created")
    print(f"  Name               {farmer['full_name']}")
    print(f"  Mobile             {farmer['mobile']}")
    print(f"  Village / District {farmer['village']}, {farmer['district']}, {farmer['state']}")
    print(f"  Status             {farmer['card_status_label']}")
    print()
    print(f"  Farmer ID          {creds['farmer_id']}")
    print(f"  Login ID           {creds['login_id']}")
    print(f"  Temporary password {creds['temporary_password']}")
    print()
    print("The temporary password is not stored anywhere readable. Sign in at /portal/login;")
    print("the farmer is asked to set a new password on first sign-in.")
    return 0


def cmd_reset(args) -> int:
    try:
        creds = service.reset_password(auth.canonical_farmer_id(args.farmer_id))
    except PortalError as exc:
        print(exc.message)
        return 1
    print(f"New temporary password for {creds['farmer_id']} ({creds['login_id']}): {creds['temporary_password']}")
    return 0


def cmd_verify(args) -> int:
    try:
        farmer = service.set_verification(auth.canonical_farmer_id(args.farmer_id), args.status.upper(), "Registry operator (CLI)")
    except PortalError as exc:
        print(exc.message)
        return 1
    print(f"{farmer['farmer_id']} is now {farmer['verification_status']} ({farmer['card_status_label']})")
    return 0


def cmd_show(args) -> int:
    farmer = service.find_farmer_by_mobile(args.mobile)
    if not farmer:
        print("No portal account for that mobile number.")
        return 1
    for key in ("farmer_id", "login_id", "full_name", "mobile", "village", "district", "state",
                "verification_status", "card_status_label", "card_serial", "must_change_password"):
        print(f"  {key:22} {farmer[key]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Farmer portal accounts")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="register a farmer and print their credentials")
    create.add_argument("--mobile", required=True)
    create.add_argument("--name")
    create.add_argument("--village")
    create.add_argument("--taluk")
    create.add_argument("--district")
    create.add_argument("--state")
    create.add_argument("--pincode")
    create.add_argument("--land", help="total land in acres")
    create.add_argument("--verify", action="store_true", help="mark the registration verified")
    create.set_defaults(func=cmd_create)

    reset = sub.add_parser("reset", help="issue a new temporary password")
    reset.add_argument("--farmer-id", required=True)
    reset.set_defaults(func=cmd_reset)

    verify = sub.add_parser("verify", help="set verification status")
    verify.add_argument("--farmer-id", required=True)
    verify.add_argument("--status", default="VERIFIED", choices=["VERIFIED", "PENDING", "SUSPENDED"])
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show", help="show the account for a mobile number")
    show.add_argument("--mobile", required=True)
    show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
