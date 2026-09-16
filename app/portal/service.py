"""
Farmer portal records: accounts, profile, farm, crops, documents,
notifications, activity and the data printed on the digital ID card.

Every function takes a farmer_id that the route layer has already resolved
from the session, so one farmer can never reach another farmer's rows.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.portal import auth
from app.portal.schema import init_portal_tables
from app.services.db import DB_LOCK, get_connection

IST = timezone(timedelta(hours=5, minutes=30))

GENDERS = ("Female", "Male", "Other", "Prefer not to say")
LANGUAGES = {"kn": "Kannada", "hi": "Hindi", "en": "English"}
OWNERSHIP = {"OWNED": "Owned", "LEASED": "Leased", "SHARED": "Shared cropping"}
SOIL_TYPES = (
    "Black soil", "Red soil", "Red laterite soil", "Laterite soil", "Alluvial soil",
    "Sandy loam", "Clay loam", "Saline or alkaline soil", "Not tested",
)
IRRIGATION_TYPES = (
    "Rainfed", "Borewell", "Open well", "Canal", "Tank or pond", "Drip irrigation",
    "Sprinkler", "River lift",
)
SEASONS = ("Kharif", "Rabi", "Zaid", "Perennial")
CROP_STATUS = {
    "PLANNED": "Planned", "SOWN": "Sown", "GROWING": "Growing",
    "HARVESTED": "Harvested", "FAILED": "Crop failed",
}
CURRENT_CROP_STATUSES = ("PLANNED", "SOWN", "GROWING")
HARVEST_UNITS = ("quintal", "kg", "tonne", "bags")
QUALITY_GRADES = ("A", "B", "C")
DOCUMENT_CATEGORIES = {
    "LAND_RECORD": "Land record (RTC / Pahani)",
    "CERTIFICATE": "Certificate",
    "BANK": "Bank passbook",
    "OTHER": "Other document",
}
NOTIFICATION_CATEGORIES = {
    "GOVERNMENT": "Government update", "ALERT": "Important alert", "SCHEME": "Scheme",
    "WEATHER": "Weather alert", "APPLICATION": "Application update",
}
VERIFICATION_STATUSES = ("PENDING", "VERIFIED", "SUSPENDED")

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENTS = 50
CARD_VALIDITY_YEARS = 5

# Fields that identify the farmer on the card: changing any of them after
# verification sends the registration back for verification.
_IDENTITY_FIELDS = ("full_name", "village", "district", "state")


class PortalError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "INVALID",
                 fields: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.fields = fields or {}


def _now() -> int:
    return int(time.time())


def _today() -> date:
    return datetime.now(IST).date()


def _connect() -> sqlite3.Connection:
    init_portal_tables()
    return get_connection()


def uploads_root() -> str:
    from app.services import db

    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "portal_uploads")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class _Form:
    """Collects field errors so a form reports every problem at once."""

    def __init__(self, data: Dict[str, Any]):
        self.data = data or {}
        self.errors: Dict[str, str] = {}

    def text(self, key: str, label: str, *, required: bool = False, min_len: int = 0,
             max_len: int = 120, pattern: Optional[str] = None, pattern_msg: str = "") -> Optional[str]:
        raw = self.data.get(key)
        value = re.sub(r"\s+", " ", str(raw)).strip() if raw is not None else ""
        if not value:
            if required:
                self.errors[key] = f"{label} is required."
            return None
        if len(value) < min_len:
            self.errors[key] = f"{label} must be at least {min_len} characters."
        elif len(value) > max_len:
            self.errors[key] = f"{label} must be at most {max_len} characters."
        elif pattern and not re.fullmatch(pattern, value):
            self.errors[key] = pattern_msg or f"{label} is not valid."
        return value

    def choice(self, key: str, label: str, options, *, required: bool = False) -> Optional[str]:
        value = self.text(key, label, required=required)
        if value is not None and value not in options:
            self.errors[key] = f"Choose a valid {label.lower()}."
        return value

    def number(self, key: str, label: str, *, required: bool = False,
               minimum: float = 0.0, maximum: float = 1e9, exclusive_min: bool = True) -> Optional[float]:
        raw = self.data.get(key)
        if raw is None or str(raw).strip() == "":
            if required:
                self.errors[key] = f"{label} is required."
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self.errors[key] = f"{label} must be a number."
            return None
        if value != value or (value <= minimum if exclusive_min else value < minimum) or value > maximum:
            low = "more than" if exclusive_min else "at least"
            self.errors[key] = f"{label} must be {low} {minimum:g} and at most {maximum:g}."
        return round(value, 3)

    def day(self, key: str, label: str, *, required: bool = False) -> Optional[date]:
        raw = str(self.data.get(key) or "").strip()
        if not raw:
            if required:
                self.errors[key] = f"{label} is required."
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            self.errors[key] = f"{label} must be a valid date."
            return None

    def raise_if_invalid(self) -> None:
        if self.errors:
            raise PortalError("Please correct the highlighted fields.", 422, "VALIDATION", self.errors)


_NAME_PATTERN = r"[^\d@#$%^&*()_+=<>?/\\|{}\[\]~`!;:\"]{2,80}"
_PINCODE_PATTERN = r"[1-9]\d{5}"
_EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"


def _sniff(data: bytes) -> Optional[Tuple[str, str]]:
    """File type from its own bytes; the browser-declared type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    return None


# ---------------------------------------------------------------------------
# Shaping rows for the API
# ---------------------------------------------------------------------------

def _date_label(ts: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(ts, IST).date().isoformat() if ts else None


def card_status(row: sqlite3.Row) -> Tuple[str, str]:
    if row["verification_status"] == "SUSPENDED":
        return "SUSPENDED", "Suspended"
    if row["valid_until"] < _now():
        return "EXPIRED", "Expired"
    if row["verification_status"] == "VERIFIED":
        return "ACTIVE", "Active"
    return "PENDING", "Pending verification"


def public_farmer(row: sqlite3.Row) -> Dict[str, Any]:
    status, status_label = card_status(row)
    return {
        "farmer_id": row["farmer_id"],
        "login_id": row["login_id"],
        "mobile": row["mobile"],
        "must_change_password": bool(row["must_change_password"]),
        "full_name": row["full_name"],
        "guardian_name": row["guardian_name"],
        "date_of_birth": row["date_of_birth"],
        "gender": row["gender"],
        "alternate_mobile": row["alternate_mobile"],
        "email": row["email"],
        "preferred_language": row["preferred_language"],
        "address_line": row["address_line"],
        "village": row["village"],
        "taluk": row["taluk"],
        "district": row["district"],
        "state": row["state"],
        "pincode": row["pincode"],
        "declared_land_acres": row["declared_land_acres"],
        "photo_available": bool(row["photo_path"]),
        "photo_version": row["updated_at"],
        "verification_status": row["verification_status"],
        "verified_at": row["verified_at"],
        "card_status": status,
        "card_status_label": status_label,
        "card_serial": row["card_serial"],
        "registered_at": row["registered_at"],
        "valid_until": row["valid_until"],
        "last_login_at": row["last_login_at"],
    }


def _load(conn: sqlite3.Connection, farmer_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM portal_farmers WHERE farmer_id = ?", (farmer_id,)).fetchone()
    if row is None:
        raise PortalError("Farmer account not found.", 404, "NOT_FOUND")
    return row


def _activity(conn: sqlite3.Connection, farmer_id: str, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO portal_activity(farmer_id, action, detail, created_at) VALUES (?,?,?,?)",
        (farmer_id, action, detail[:300], _now()),
    )


def _notify(conn: sqlite3.Connection, farmer_id: Optional[str], category: str, title: str, body: str,
            *, severity: str = "INFO", source: str = "AI Krishi", dedupe_key: Optional[str] = None,
            link: Optional[str] = None, expires_at: Optional[int] = None) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO portal_notifications
           (id, farmer_id, category, severity, title, body, source, dedupe_key, link, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (uuid.uuid4().hex, farmer_id, category, severity, title, body, source, dedupe_key, link,
         _now(), expires_at),
    )


def _add_years_minus_day(ts: int, years: int) -> int:
    d = datetime.fromtimestamp(ts, IST)
    try:
        later = d.replace(year=d.year + years)
    except ValueError:
        later = d.replace(year=d.year + years, day=28)
    end = (later - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    return int(end.timestamp())


# ---------------------------------------------------------------------------
# Registration and login
# ---------------------------------------------------------------------------

def register_farmer(data: Dict[str, Any], *, require_consent: bool = True) -> Dict[str, Any]:
    form = _Form(data)
    full_name = form.text("full_name", "Full name", required=True, min_len=2, max_len=80,
                          pattern=_NAME_PATTERN, pattern_msg="Use letters only in the name.")
    mobile_raw = form.text("mobile", "Mobile number", required=True)
    mobile = auth.normalise_mobile(mobile_raw or "")
    if mobile_raw and not mobile:
        form.errors["mobile"] = "Enter a valid 10-digit Indian mobile number."
    village = form.text("village", "Village", required=True, min_len=2, max_len=60)
    taluk = form.text("taluk", "Taluk", max_len=60)
    district = form.text("district", "District", required=True, min_len=2, max_len=60)
    state = form.choice("state", "State", auth.STATE_CODES, required=True)
    pincode = form.text("pincode", "PIN code", pattern=_PINCODE_PATTERN, pattern_msg="Enter a 6-digit PIN code.")
    land = form.number("declared_land_acres", "Total land", maximum=1000)
    if require_consent and not data.get("consent"):
        form.errors["consent"] = "Confirm that the details are correct to continue."
    form.raise_if_invalid()

    now = _now()
    year = datetime.fromtimestamp(now, IST).year
    temporary_password = auth.generate_temporary_password()

    with DB_LOCK:
        conn = _connect()
        try:
            if conn.execute("SELECT 1 FROM portal_farmers WHERE mobile = ?", (mobile,)).fetchone():
                raise PortalError(
                    "This mobile number is already registered. Sign in instead.", 409, "MOBILE_TAKEN",
                    {"mobile": "This mobile number is already registered."},
                )

            scope = f"{auth.state_code(state)}-{auth.district_code(district)}-{year}"
            conn.execute(
                "INSERT INTO portal_sequences(scope, value) VALUES (?, 1) "
                "ON CONFLICT(scope) DO UPDATE SET value = value + 1",
                (scope,),
            )
            sequence = conn.execute("SELECT value FROM portal_sequences WHERE scope = ?", (scope,)).fetchone()[0]
            farmer_id = auth.format_farmer_id(state, district, year, sequence)

            base = auth.login_id_base(full_name, mobile)
            login_id, suffix = base, 0
            while conn.execute("SELECT 1 FROM portal_farmers WHERE login_id = ?", (login_id,)).fetchone():
                suffix += 1
                login_id = f"{base}{chr(ord('a') + suffix - 1)}" if suffix <= 26 else f"{base}{suffix}"

            serial = auth.card_serial(year)
            while conn.execute("SELECT 1 FROM portal_farmers WHERE card_serial = ?", (serial,)).fetchone():
                serial = auth.card_serial(year)

            conn.execute(
                """INSERT INTO portal_farmers
                   (farmer_id, login_id, mobile, password_hash, must_change_password, full_name, village,
                    taluk, district, state, pincode, declared_land_acres, card_serial, registered_at,
                    valid_until, updated_at)
                   VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)""",
                (farmer_id, login_id, mobile, auth.hash_password(temporary_password), full_name, village,
                 taluk, district, state, pincode, land, serial, now,
                 _add_years_minus_day(now, CARD_VALIDITY_YEARS), now),
            )
            _activity(conn, farmer_id, "ACCOUNT_CREATED", f"Farmer ID {farmer_id} issued")
            _notify(conn, farmer_id, "APPLICATION", "Farmer ID issued",
                    f"Your Farmer ID {farmer_id} has been issued and your digital ID card is ready. "
                    "Your registration will be marked verified after the details are checked.",
                    link="/portal/id-card", dedupe_key=f"registered:{farmer_id}")
            conn.commit()
            row = _load(conn, farmer_id)
        finally:
            conn.close()

    return {
        "farmer": public_farmer(row),
        "credentials": {
            "farmer_id": farmer_id,
            "login_id": login_id,
            "temporary_password": temporary_password,
        },
    }


# Hashing a dummy password when the account does not exist keeps a failed
# login for an unknown ID as slow as one for a real ID.
_DUMMY_HASH = auth.hash_password("unused-dummy-password")


def authenticate(identifier: str, password: str, user_agent: str = "") -> Tuple[str, Dict[str, Any]]:
    ident = (identifier or "").strip()
    if not ident or not password:
        raise PortalError("Enter your login ID and password.", 422, "VALIDATION", {
            **({"identifier": "Login ID is required."} if not ident else {}),
            **({"password": "Password is required."} if not password else {}),
        })

    now = _now()
    with DB_LOCK:
        conn = _connect()
        try:
            mobile = auth.normalise_mobile(ident)
            row = conn.execute(
                "SELECT * FROM portal_farmers WHERE farmer_id = ? OR lower(login_id) = lower(?) OR mobile = ?",
                (auth.canonical_farmer_id(ident), ident, mobile or ""),
            ).fetchone()

            if row is None:
                auth.verify_password(password, _DUMMY_HASH)
                raise PortalError("The login ID or password is incorrect.", 401, "BAD_CREDENTIALS")

            if row["locked_until"] and row["locked_until"] > now:
                minutes = max(1, round((row["locked_until"] - now) / 60))
                raise PortalError(
                    f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                    429, "LOCKED",
                )

            if not auth.verify_password(password, row["password_hash"]):
                failed = row["failed_logins"] + 1
                locked_until = now + auth.LOCKOUT_SECONDS if failed >= auth.MAX_FAILED_LOGINS else None
                conn.execute(
                    "UPDATE portal_farmers SET failed_logins = ?, locked_until = ? WHERE farmer_id = ?",
                    (0 if locked_until else failed, locked_until, row["farmer_id"]),
                )
                if locked_until:
                    _activity(conn, row["farmer_id"], "ACCOUNT_LOCKED", "Locked after repeated failed sign-in attempts")
                conn.commit()
                if locked_until:
                    raise PortalError(
                        "Too many failed attempts. Your account is locked for 15 minutes.", 429, "LOCKED",
                    )
                raise PortalError("The login ID or password is incorrect.", 401, "BAD_CREDENTIALS")

            if row["verification_status"] == "SUSPENDED":
                raise PortalError(
                    "This account is suspended. Contact the AI Krishi helpline.", 403, "SUSPENDED",
                )

            token, token_hash = auth.new_session_token()
            conn.execute("DELETE FROM portal_sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO portal_sessions(token_hash, farmer_id, created_at, expires_at, user_agent) VALUES (?,?,?,?,?)",
                (token_hash, row["farmer_id"], now, now + auth.SESSION_SECONDS, (user_agent or "")[:200]),
            )
            conn.execute(
                "UPDATE portal_farmers SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE farmer_id = ?",
                (now, row["farmer_id"]),
            )
            _activity(conn, row["farmer_id"], "SIGNED_IN", "Signed in to the farmer portal")
            conn.commit()
            return token, public_farmer(_load(conn, row["farmer_id"]))
        finally:
            conn.close()


def session_farmer(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT f.* FROM portal_sessions s JOIN portal_farmers f ON f.farmer_id = s.farmer_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (auth.hash_token(token), _now()),
            ).fetchone()
            if row is None or row["verification_status"] == "SUSPENDED":
                return None
            return public_farmer(row)
        finally:
            conn.close()


def end_session(token: Optional[str]) -> None:
    if not token:
        return
    with DB_LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM portal_sessions WHERE token_hash = ?", (auth.hash_token(token),))
            conn.commit()
        finally:
            conn.close()


def change_password(farmer_id: str, current_password: str, new_password: str, keep_token: Optional[str]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            if not auth.verify_password(current_password or "", row["password_hash"]):
                raise PortalError("Please correct the highlighted fields.", 422, "VALIDATION",
                                  {"current_password": "The current password is incorrect."})
            problems = auth.password_problems(new_password, mobile=row["mobile"], login_id=row["login_id"])
            if not problems and new_password == current_password:
                problems.append("The new password must be different from the current one.")
            if problems:
                raise PortalError("Please correct the highlighted fields.", 422, "VALIDATION",
                                  {"new_password": " ".join(problems)})

            conn.execute(
                "UPDATE portal_farmers SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE farmer_id = ?",
                (auth.hash_password(new_password), _now(), farmer_id),
            )
            conn.execute(
                "DELETE FROM portal_sessions WHERE farmer_id = ? AND token_hash != ?",
                (farmer_id, auth.hash_token(keep_token or "")),
            )
            _activity(conn, farmer_id, "PASSWORD_CHANGED", "Password changed")
            _notify(conn, farmer_id, "ALERT", "Your password was changed",
                    "The password for your farmer account was changed and other signed-in devices were signed out. "
                    "If you did not do this, contact the AI Krishi helpline immediately.",
                    severity="WARNING")
            conn.commit()
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def reset_password(farmer_id: str) -> Dict[str, Any]:
    """Operator action: issue a new temporary password and sign the farmer out everywhere."""
    temporary_password = auth.generate_temporary_password()
    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            conn.execute(
                """UPDATE portal_farmers SET password_hash = ?, must_change_password = 1, failed_logins = 0,
                   locked_until = NULL, updated_at = ? WHERE farmer_id = ?""",
                (auth.hash_password(temporary_password), _now(), farmer_id),
            )
            conn.execute("DELETE FROM portal_sessions WHERE farmer_id = ?", (farmer_id,))
            _activity(conn, farmer_id, "PASSWORD_RESET", "Temporary password issued by the registry office")
            conn.commit()
            return {"farmer_id": farmer_id, "login_id": row["login_id"], "temporary_password": temporary_password}
        finally:
            conn.close()


def set_verification(farmer_id: str, status: str, verified_by: str = "Registry operator") -> Dict[str, Any]:
    if status not in VERIFICATION_STATUSES:
        raise PortalError("Unknown verification status.", 422, "VALIDATION")
    now = _now()
    with DB_LOCK:
        conn = _connect()
        try:
            _load(conn, farmer_id)
            conn.execute(
                "UPDATE portal_farmers SET verification_status = ?, verified_at = ?, verified_by = ?, updated_at = ? WHERE farmer_id = ?",
                (status, now if status == "VERIFIED" else None,
                 verified_by if status == "VERIFIED" else None, now, farmer_id),
            )
            if status == "SUSPENDED":
                conn.execute("DELETE FROM portal_sessions WHERE farmer_id = ?", (farmer_id,))
            messages = {
                "VERIFIED": ("Registration verified",
                             "Your farmer registration has been verified. Your digital ID card now shows Active status."),
                "PENDING": ("Registration pending verification",
                            "Your registration is waiting for verification by the registry office."),
                "SUSPENDED": ("Registration suspended",
                              "Your registration has been suspended. Contact the AI Krishi helpline for details."),
            }
            title, body = messages[status]
            _notify(conn, farmer_id, "APPLICATION", title, body,
                    severity="WARNING" if status == "SUSPENDED" else "INFO", link="/portal/id-card")
            _activity(conn, farmer_id, f"VERIFICATION_{status}", title)
            conn.commit()
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def find_farmer_by_mobile(mobile: str) -> Optional[Dict[str, Any]]:
    normalised = auth.normalise_mobile(mobile)
    if not normalised:
        return None
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM portal_farmers WHERE mobile = ?", (normalised,)).fetchone()
            return public_farmer(row) if row else None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def get_profile(farmer_id: str) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def update_profile(farmer_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    form = _Form(data)
    values = {
        "full_name": form.text("full_name", "Full name", required=True, min_len=2, max_len=80,
                               pattern=_NAME_PATTERN, pattern_msg="Use letters only in the name."),
        "guardian_name": form.text("guardian_name", "Father's or spouse's name", max_len=80,
                                   pattern=_NAME_PATTERN, pattern_msg="Use letters only in the name."),
        "gender": form.choice("gender", "Gender", GENDERS),
        "email": form.text("email", "Email", max_len=120, pattern=_EMAIL_PATTERN,
                           pattern_msg="Enter a valid email address."),
        "preferred_language": form.choice("preferred_language", "Preferred language", LANGUAGES, required=True),
        "address_line": form.text("address_line", "House / street", max_len=160),
        "village": form.text("village", "Village", required=True, min_len=2, max_len=60),
        "taluk": form.text("taluk", "Taluk", max_len=60),
        "district": form.text("district", "District", required=True, min_len=2, max_len=60),
        "state": form.choice("state", "State", auth.STATE_CODES, required=True),
        "pincode": form.text("pincode", "PIN code", pattern=_PINCODE_PATTERN, pattern_msg="Enter a 6-digit PIN code."),
        "declared_land_acres": form.number("declared_land_acres", "Total land", maximum=1000),
    }

    dob = form.day("date_of_birth", "Date of birth")
    if dob:
        age = (_today() - dob).days // 365
        if dob > _today() or age < 16 or age > 110:
            form.errors["date_of_birth"] = "Enter a date of birth for an age between 16 and 110."
    values["date_of_birth"] = dob.isoformat() if dob else None

    alternate = form.text("alternate_mobile", "Alternate mobile")
    values["alternate_mobile"] = None
    if alternate:
        normalised = auth.normalise_mobile(alternate)
        if not normalised:
            form.errors["alternate_mobile"] = "Enter a valid 10-digit Indian mobile number."
        values["alternate_mobile"] = normalised
    form.raise_if_invalid()

    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            if values["alternate_mobile"] and values["alternate_mobile"] == row["mobile"]:
                raise PortalError("Please correct the highlighted fields.", 422, "VALIDATION",
                                  {"alternate_mobile": "The alternate number must differ from your registered mobile."})

            changed = [k for k, v in values.items() if (row[k] if row[k] != "" else None) != v]
            if not changed:
                return public_farmer(row)

            identity_changed = [k for k in changed if k in _IDENTITY_FIELDS]
            reverify = row["verification_status"] == "VERIFIED" and identity_changed

            assignments = ", ".join(f"{k} = ?" for k in values)
            conn.execute(
                f"UPDATE portal_farmers SET {assignments}, updated_at = ? WHERE farmer_id = ?",
                (*values.values(), _now(), farmer_id),
            )
            if reverify:
                conn.execute(
                    "UPDATE portal_farmers SET verification_status = 'PENDING', verified_at = NULL, verified_by = NULL WHERE farmer_id = ?",
                    (farmer_id,),
                )
                _notify(conn, farmer_id, "APPLICATION", "Changes sent for re-verification",
                        "You changed details printed on your ID card. Your registration is pending verification "
                        "again until the registry office checks the new details.",
                        severity="WARNING", link="/portal/id-card")
            labels = {"full_name": "name", "guardian_name": "guardian name", "date_of_birth": "date of birth",
                      "alternate_mobile": "alternate mobile", "preferred_language": "language",
                      "address_line": "address", "declared_land_acres": "land area"}
            _activity(conn, farmer_id, "PROFILE_UPDATED",
                      "Updated " + ", ".join(labels.get(k, k.replace("_", " ")) for k in changed))
            conn.commit()
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def save_photo(farmer_id: str, data: bytes) -> Dict[str, Any]:
    if not data:
        raise PortalError("The photo is empty.", 422, "VALIDATION", {"photo": "Choose a photo to upload."})
    if len(data) > MAX_PHOTO_BYTES:
        raise PortalError("The photo is larger than 5 MB.", 413, "TOO_LARGE", {"photo": "Use a photo under 5 MB."})
    sniffed = _sniff(data)
    if not sniffed or not sniffed[0].startswith("image/"):
        raise PortalError("Upload a JPG or PNG photo.", 415, "UNSUPPORTED", {"photo": "Upload a JPG or PNG photo."})
    mime, ext = sniffed

    folder = os.path.join(uploads_root(), farmer_id)
    os.makedirs(folder, exist_ok=True)
    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            path = os.path.join(folder, f"photo-{uuid.uuid4().hex[:12]}.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            conn.execute(
                "UPDATE portal_farmers SET photo_path = ?, photo_mime = ?, updated_at = ? WHERE farmer_id = ?",
                (path, mime, _now(), farmer_id),
            )
            _activity(conn, farmer_id, "PHOTO_UPDATED", "Profile photograph updated")
            conn.commit()
            old = row["photo_path"]
            if old and old != path and os.path.isfile(old):
                os.remove(old)
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def remove_photo(farmer_id: str) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            conn.execute(
                "UPDATE portal_farmers SET photo_path = NULL, photo_mime = NULL, updated_at = ? WHERE farmer_id = ?",
                (_now(), farmer_id),
            )
            _activity(conn, farmer_id, "PHOTO_REMOVED", "Profile photograph removed")
            conn.commit()
            if row["photo_path"] and os.path.isfile(row["photo_path"]):
                os.remove(row["photo_path"])
            return public_farmer(_load(conn, farmer_id))
        finally:
            conn.close()


def photo_file(farmer_id: str) -> Optional[Tuple[str, str]]:
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT photo_path, photo_mime FROM portal_farmers WHERE farmer_id = ?", (farmer_id,),
            ).fetchone()
        finally:
            conn.close()
    if row and row["photo_path"] and os.path.isfile(row["photo_path"]):
        return row["photo_path"], row["photo_mime"]
    return None


# ---------------------------------------------------------------------------
# Farm and plots
# ---------------------------------------------------------------------------

def _plot_values(form: _Form, farmer: sqlite3.Row) -> Dict[str, Any]:
    values = {
        "name": form.text("name", "Plot name", required=True, min_len=2, max_len=60),
        "survey_number": form.text("survey_number", "Survey number", max_len=40),
        "area_acres": form.number("area_acres", "Area", required=True, maximum=1000),
        "ownership": form.choice("ownership", "Ownership", OWNERSHIP, required=True),
        "village": form.text("village", "Village", max_len=60) or farmer["village"],
        "taluk": form.text("taluk", "Taluk", max_len=60) or farmer["taluk"],
        "district": form.text("district", "District", max_len=60) or farmer["district"],
        "soil_type": form.choice("soil_type", "Soil type", SOIL_TYPES),
        "irrigation_type": form.choice("irrigation_type", "Irrigation", IRRIGATION_TYPES),
    }
    lat = form.number("latitude", "Latitude", minimum=-90, maximum=90, exclusive_min=False)
    lng = form.number("longitude", "Longitude", minimum=-180, maximum=180, exclusive_min=False)
    if (lat is None) != (lng is None):
        form.errors["latitude" if lat is None else "longitude"] = "Enter both latitude and longitude, or neither."
    values["latitude"], values["longitude"] = lat, lng
    return values


def _plot_row(r: sqlite3.Row, used: float = 0.0, crop_count: int = 0) -> Dict[str, Any]:
    return {
        "id": r["id"], "name": r["name"], "survey_number": r["survey_number"],
        "area_acres": r["area_acres"], "ownership": r["ownership"],
        "ownership_label": OWNERSHIP.get(r["ownership"], r["ownership"]),
        "village": r["village"], "taluk": r["taluk"], "district": r["district"],
        "latitude": r["latitude"], "longitude": r["longitude"],
        "soil_type": r["soil_type"], "irrigation_type": r["irrigation_type"],
        "area_in_use": round(used, 3), "active_crops": crop_count,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def create_plot(farmer_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            farmer = _load(conn, farmer_id)
            form = _Form(data)
            values = _plot_values(form, farmer)
            form.raise_if_invalid()
            if conn.execute("SELECT COUNT(*) FROM portal_plots WHERE farmer_id = ?", (farmer_id,)).fetchone()[0] >= 100:
                raise PortalError("A farmer can record at most 100 plots.", 422, "LIMIT")
            plot_id = uuid.uuid4().hex
            now = _now()
            conn.execute(
                f"INSERT INTO portal_plots(id, farmer_id, {', '.join(values)}, created_at, updated_at) "
                f"VALUES (?, ?, {', '.join('?' for _ in values)}, ?, ?)",
                (plot_id, farmer_id, *values.values(), now, now),
            )
            _activity(conn, farmer_id, "PLOT_ADDED", f"Added plot {values['name']} ({values['area_acres']:g} acres)")
            conn.commit()
            return _plot_row(conn.execute("SELECT * FROM portal_plots WHERE id = ?", (plot_id,)).fetchone())
        finally:
            conn.close()


def update_plot(farmer_id: str, plot_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            farmer = _load(conn, farmer_id)
            existing = conn.execute(
                "SELECT * FROM portal_plots WHERE id = ? AND farmer_id = ?", (plot_id, farmer_id),
            ).fetchone()
            if existing is None:
                raise PortalError("Plot not found.", 404, "NOT_FOUND")
            form = _Form(data)
            values = _plot_values(form, farmer)
            in_use = conn.execute(
                f"SELECT COALESCE(SUM(area_acres), 0) FROM portal_crops WHERE plot_id = ? AND status IN "
                f"({','.join('?' for _ in CURRENT_CROP_STATUSES)})",
                (plot_id, *CURRENT_CROP_STATUSES),
            ).fetchone()[0]
            if values["area_acres"] is not None and values["area_acres"] + 1e-6 < in_use:
                form.errors["area_acres"] = f"Current crops already use {in_use:g} acres of this plot."
            form.raise_if_invalid()
            conn.execute(
                f"UPDATE portal_plots SET {', '.join(f'{k} = ?' for k in values)}, updated_at = ? WHERE id = ?",
                (*values.values(), _now(), plot_id),
            )
            _activity(conn, farmer_id, "PLOT_UPDATED", f"Updated plot {values['name']}")
            conn.commit()
            return _plot_row(conn.execute("SELECT * FROM portal_plots WHERE id = ?", (plot_id,)).fetchone())
        finally:
            conn.close()


def delete_plot(farmer_id: str, plot_id: str) -> None:
    with DB_LOCK:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT name FROM portal_plots WHERE id = ? AND farmer_id = ?", (plot_id, farmer_id),
            ).fetchone()
            if existing is None:
                raise PortalError("Plot not found.", 404, "NOT_FOUND")
            active = conn.execute(
                f"SELECT COUNT(*) FROM portal_crops WHERE plot_id = ? AND status IN "
                f"({','.join('?' for _ in CURRENT_CROP_STATUSES)})",
                (plot_id, *CURRENT_CROP_STATUSES),
            ).fetchone()[0]
            if active:
                raise PortalError(
                    f"This plot has {active} current crop{'s' if active != 1 else ''}. "
                    "Mark them harvested or move them to another plot first.", 409, "IN_USE",
                )
            conn.execute("DELETE FROM portal_plots WHERE id = ?", (plot_id,))
            _activity(conn, farmer_id, "PLOT_REMOVED", f"Removed plot {existing['name']}")
            conn.commit()
        finally:
            conn.close()


def farm_summary(farmer_id: str) -> Dict[str, Any]:
    from app.agriculture import get_soil_info

    with DB_LOCK:
        conn = _connect()
        try:
            farmer = _load(conn, farmer_id)
            plots = conn.execute(
                "SELECT * FROM portal_plots WHERE farmer_id = ? ORDER BY created_at", (farmer_id,),
            ).fetchall()
            usage = {
                r["plot_id"]: (r["used"], r["n"])
                for r in conn.execute(
                    f"SELECT plot_id, SUM(area_acres) AS used, COUNT(*) AS n FROM portal_crops "
                    f"WHERE farmer_id = ? AND plot_id IS NOT NULL AND status IN "
                    f"({','.join('?' for _ in CURRENT_CROP_STATUSES)}) GROUP BY plot_id",
                    (farmer_id, *CURRENT_CROP_STATUSES),
                )
            }
        finally:
            conn.close()

    rows = [_plot_row(p, *usage.get(p["id"], (0.0, 0))) for p in plots]
    total = round(sum(p["area_acres"] for p in rows), 3)
    irrigated = round(sum(p["area_acres"] for p in rows if p["irrigation_type"] not in (None, "Rainfed")), 3)
    by_ownership = {k: round(sum(p["area_acres"] for p in rows if p["ownership"] == k), 3) for k in OWNERSHIP}

    soil = get_soil_info(farmer["district"] or "Karnataka")
    return {
        "plots": rows,
        "totals": {
            "plots": len(rows),
            "total_acres": total,
            "declared_acres": farmer["declared_land_acres"],
            "irrigated_acres": irrigated,
            "rainfed_acres": round(total - irrigated, 3),
            "by_ownership": by_ownership,
            "in_use_acres": round(sum(p["area_in_use"] for p in rows), 3),
        },
        "location": {
            "village": farmer["village"], "taluk": farmer["taluk"], "district": farmer["district"],
            "state": farmer["state"], "pincode": farmer["pincode"],
        },
        "soil_reference": {
            "region": soil.get("name"),
            "soil_type": soil.get("soil_type"),
            "ph_range": soil.get("ph_range"),
            "characteristics": soil.get("characteristics"),
            "suitable_crops": soil.get("suitable_crops") or [],
            "source": "AI Krishi regional soil dataset (district level, not a field test)",
        },
    }


# ---------------------------------------------------------------------------
# Crops and harvests
# ---------------------------------------------------------------------------

def crop_names() -> List[str]:
    from app.market.normalization import COMMODITY_MASTER

    return sorted(c.canonical for c in COMMODITY_MASTER)


def _crop_values(conn: sqlite3.Connection, farmer_id: str, form: _Form, crop_id: Optional[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "crop_name": form.text("crop_name", "Crop", required=True, min_len=2, max_len=40),
        "variety": form.text("variety", "Variety", max_len=60),
        "season": form.choice("season", "Season", SEASONS, required=True),
        "area_acres": form.number("area_acres", "Area cultivated", required=True, maximum=1000),
        "seed_requirement": form.text("seed_requirement", "Seed requirement", max_len=300),
        "fertilizer_plan": form.text("fertilizer_plan", "Fertiliser plan", max_len=300),
        "pesticide_plan": form.text("pesticide_plan", "Plant protection plan", max_len=300),
        "irrigation_plan": form.text("irrigation_plan", "Irrigation plan", max_len=300),
        "status": form.choice("status", "Status", CROP_STATUS, required=True),
        "notes": form.text("notes", "Notes", max_len=500),
    }
    sowing = form.day("sowing_date", "Sowing date", required=True)
    harvest = form.day("expected_harvest_date", "Expected harvest date")
    if sowing and harvest and harvest < sowing:
        form.errors["expected_harvest_date"] = "Expected harvest must be on or after the sowing date."
    if sowing and values["status"] in ("SOWN", "GROWING", "HARVESTED") and sowing > _today():
        form.errors["sowing_date"] = "The sowing date is in the future. Set the status to Planned."
    values["sowing_date"] = sowing.isoformat() if sowing else None
    values["expected_harvest_date"] = harvest.isoformat() if harvest else None

    plot_id = str(form.data.get("plot_id") or "").strip() or None
    values["plot_id"] = plot_id
    if plot_id:
        plot = conn.execute(
            "SELECT area_acres FROM portal_plots WHERE id = ? AND farmer_id = ?", (plot_id, farmer_id),
        ).fetchone()
        if plot is None:
            form.errors["plot_id"] = "Choose one of your plots."
        elif values["area_acres"] and values["status"] in CURRENT_CROP_STATUSES:
            used = conn.execute(
                f"SELECT COALESCE(SUM(area_acres), 0) FROM portal_crops WHERE plot_id = ? AND id != ? AND status IN "
                f"({','.join('?' for _ in CURRENT_CROP_STATUSES)})",
                (plot_id, crop_id or "", *CURRENT_CROP_STATUSES),
            ).fetchone()[0]
            free = round(plot["area_acres"] - used, 3)
            if values["area_acres"] > free + 1e-6:
                form.errors["area_acres"] = f"Only {max(free, 0):g} acres of this plot are free for another current crop."
    return values


def _crop_row(r: sqlite3.Row, harvests: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": r["id"], "plot_id": r["plot_id"], "plot_name": r["plot_name"],
        "crop_name": r["crop_name"], "variety": r["variety"], "season": r["season"],
        "sowing_date": r["sowing_date"], "expected_harvest_date": r["expected_harvest_date"],
        "area_acres": r["area_acres"], "seed_requirement": r["seed_requirement"],
        "fertilizer_plan": r["fertilizer_plan"], "pesticide_plan": r["pesticide_plan"],
        "irrigation_plan": r["irrigation_plan"], "status": r["status"],
        "status_label": CROP_STATUS.get(r["status"], r["status"]), "notes": r["notes"],
        "is_current": r["status"] in CURRENT_CROP_STATUSES,
        "harvests": harvests,
        "harvest_count": len(harvests),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_crops(farmer_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = _connect()
        try:
            crops = conn.execute(
                """SELECT c.*, p.name AS plot_name FROM portal_crops c
                   LEFT JOIN portal_plots p ON p.id = c.plot_id
                   WHERE c.farmer_id = ?
                   ORDER BY CASE WHEN c.status IN ('PLANNED','SOWN','GROWING') THEN 0 ELSE 1 END,
                            c.sowing_date DESC, c.created_at DESC""",
                (farmer_id,),
            ).fetchall()
            harvests: Dict[str, List[Dict[str, Any]]] = {}
            for h in conn.execute(
                "SELECT * FROM portal_harvests WHERE farmer_id = ? ORDER BY harvest_date DESC, created_at DESC",
                (farmer_id,),
            ):
                harvests.setdefault(h["crop_id"], []).append({
                    "id": h["id"], "harvest_date": h["harvest_date"], "quantity": h["quantity"],
                    "unit": h["unit"], "quality_grade": h["quality_grade"], "notes": h["notes"],
                    "created_at": h["created_at"],
                })
            return [_crop_row(c, harvests.get(c["id"], [])) for c in crops]
        finally:
            conn.close()


def _one_crop(conn: sqlite3.Connection, farmer_id: str, crop_id: str) -> sqlite3.Row:
    row = conn.execute(
        """SELECT c.*, p.name AS plot_name FROM portal_crops c LEFT JOIN portal_plots p ON p.id = c.plot_id
           WHERE c.id = ? AND c.farmer_id = ?""",
        (crop_id, farmer_id),
    ).fetchone()
    if row is None:
        raise PortalError("Crop not found.", 404, "NOT_FOUND")
    return row


def create_crop(farmer_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            _load(conn, farmer_id)
            form = _Form(data)
            values = _crop_values(conn, farmer_id, form, None)
            form.raise_if_invalid()
            crop_id = uuid.uuid4().hex
            now = _now()
            conn.execute(
                f"INSERT INTO portal_crops(id, farmer_id, {', '.join(values)}, created_at, updated_at) "
                f"VALUES (?, ?, {', '.join('?' for _ in values)}, ?, ?)",
                (crop_id, farmer_id, *values.values(), now, now),
            )
            _activity(conn, farmer_id, "CROP_ADDED",
                      f"Added {values['crop_name']} ({values['season']}, {values['area_acres']:g} acres)")
            conn.commit()
            return _crop_row(_one_crop(conn, farmer_id, crop_id), [])
        finally:
            conn.close()


def update_crop(farmer_id: str, crop_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            before = _one_crop(conn, farmer_id, crop_id)
            form = _Form(data)
            values = _crop_values(conn, farmer_id, form, crop_id)
            form.raise_if_invalid()
            conn.execute(
                f"UPDATE portal_crops SET {', '.join(f'{k} = ?' for k in values)}, updated_at = ? WHERE id = ?",
                (*values.values(), _now(), crop_id),
            )
            detail = f"Updated {values['crop_name']}"
            if before["status"] != values["status"]:
                detail += f": status {CROP_STATUS[before['status']]} to {CROP_STATUS[values['status']]}"
            _activity(conn, farmer_id, "CROP_UPDATED", detail)
            conn.commit()
        finally:
            conn.close()
    return next(c for c in list_crops(farmer_id) if c["id"] == crop_id)


def delete_crop(farmer_id: str, crop_id: str) -> None:
    with DB_LOCK:
        conn = _connect()
        try:
            crop = _one_crop(conn, farmer_id, crop_id)
            conn.execute("DELETE FROM portal_crops WHERE id = ?", (crop_id,))
            _activity(conn, farmer_id, "CROP_REMOVED", f"Removed {crop['crop_name']} record")
            conn.commit()
        finally:
            conn.close()


def add_harvest(farmer_id: str, crop_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            crop = _one_crop(conn, farmer_id, crop_id)
            form = _Form(data)
            harvest_date = form.day("harvest_date", "Harvest date", required=True)
            quantity = form.number("quantity", "Quantity", required=True, maximum=1_000_000)
            unit = form.choice("unit", "Unit", HARVEST_UNITS, required=True)
            grade = form.choice("quality_grade", "Quality grade", QUALITY_GRADES)
            notes = form.text("notes", "Notes", max_len=300)
            if harvest_date:
                if harvest_date > _today():
                    form.errors["harvest_date"] = "The harvest date cannot be in the future."
                elif harvest_date < date.fromisoformat(crop["sowing_date"]):
                    form.errors["harvest_date"] = "The harvest date cannot be before the sowing date."
            form.raise_if_invalid()
            conn.execute(
                """INSERT INTO portal_harvests(id, crop_id, farmer_id, harvest_date, quantity, unit, quality_grade, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, crop_id, farmer_id, harvest_date.isoformat(), quantity, unit, grade, notes, _now()),
            )
            _activity(conn, farmer_id, "HARVEST_RECORDED", f"Recorded {quantity:g} {unit} of {crop['crop_name']}")
            conn.commit()
        finally:
            conn.close()
    return next(c for c in list_crops(farmer_id) if c["id"] == crop_id)


def delete_harvest(farmer_id: str, harvest_id: str) -> None:
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT h.quantity, h.unit, c.crop_name FROM portal_harvests h JOIN portal_crops c ON c.id = h.crop_id
                   WHERE h.id = ? AND h.farmer_id = ?""",
                (harvest_id, farmer_id),
            ).fetchone()
            if row is None:
                raise PortalError("Harvest record not found.", 404, "NOT_FOUND")
            conn.execute("DELETE FROM portal_harvests WHERE id = ?", (harvest_id,))
            _activity(conn, farmer_id, "HARVEST_REMOVED",
                      f"Removed harvest record of {row['quantity']:g} {row['unit']} {row['crop_name']}")
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def _document_row(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": r["id"], "category": r["category"],
        "category_label": DOCUMENT_CATEGORIES.get(r["category"], r["category"]),
        "title": r["title"], "reference_number": r["reference_number"],
        "original_name": r["original_name"], "mime": r["mime"], "size_bytes": r["size_bytes"],
        "uploaded_at": r["uploaded_at"],
    }


def list_documents(farmer_id: str) -> Dict[str, Any]:
    with DB_LOCK:
        conn = _connect()
        try:
            farmer = _load(conn, farmer_id)
            rows = conn.execute(
                "SELECT * FROM portal_documents WHERE farmer_id = ? ORDER BY uploaded_at DESC", (farmer_id,),
            ).fetchall()
        finally:
            conn.close()
    status, label = card_status(farmer)
    return {
        "issued": [{
            "type": "FARMER_ID_CARD",
            "title": "Digital Farmer ID Card",
            "reference_number": farmer["farmer_id"],
            "serial": farmer["card_serial"],
            "issued_at": farmer["registered_at"],
            "valid_until": farmer["valid_until"],
            "status": status,
            "status_label": label,
        }],
        "uploaded": [_document_row(r) for r in rows],
        "limits": {"max_bytes": MAX_DOCUMENT_BYTES, "max_documents": MAX_DOCUMENTS},
    }


def upload_document(farmer_id: str, *, category: str, title: str, reference_number: str,
                    original_name: str, data: bytes) -> Dict[str, Any]:
    form = _Form({"category": category, "title": title, "reference_number": reference_number})
    category = form.choice("category", "Document type", DOCUMENT_CATEGORIES, required=True)
    title = form.text("title", "Document name", required=True, min_len=2, max_len=80)
    reference_number = form.text("reference_number", "Reference number", max_len=60)
    if not data:
        form.errors["file"] = "Choose a file to upload."
    elif len(data) > MAX_DOCUMENT_BYTES:
        form.errors["file"] = "The file is larger than 5 MB."
    sniffed = _sniff(data) if data else None
    if data and not sniffed:
        form.errors["file"] = "Upload a PDF, JPG or PNG file."
    form.raise_if_invalid()
    mime, ext = sniffed

    folder = os.path.join(uploads_root(), farmer_id)
    os.makedirs(folder, exist_ok=True)
    with DB_LOCK:
        conn = _connect()
        try:
            _load(conn, farmer_id)
            if conn.execute("SELECT COUNT(*) FROM portal_documents WHERE farmer_id = ?", (farmer_id,)).fetchone()[0] >= MAX_DOCUMENTS:
                raise PortalError(f"You can keep at most {MAX_DOCUMENTS} documents. Remove one first.", 422, "LIMIT")
            doc_id = uuid.uuid4().hex
            path = os.path.join(folder, f"doc-{doc_id}.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            safe_name = re.sub(r"[^\w.\- ]", "_", os.path.basename(original_name or f"document.{ext}"))[:120]
            conn.execute(
                """INSERT INTO portal_documents(id, farmer_id, category, title, reference_number, original_name,
                   stored_path, mime, size_bytes, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, farmer_id, category, title, reference_number, safe_name, path, mime, len(data), _now()),
            )
            _activity(conn, farmer_id, "DOCUMENT_UPLOADED", f"Uploaded {DOCUMENT_CATEGORIES[category].lower()}: {title}")
            _notify(conn, farmer_id, "APPLICATION", "Document received",
                    f"We received “{title}” ({DOCUMENT_CATEGORIES[category]}). It is stored in your documents.",
                    link="/portal/documents")
            conn.commit()
            return _document_row(conn.execute("SELECT * FROM portal_documents WHERE id = ?", (doc_id,)).fetchone())
        finally:
            conn.close()


def document_file(farmer_id: str, doc_id: str) -> Tuple[str, str, str]:
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT stored_path, mime, original_name FROM portal_documents WHERE id = ? AND farmer_id = ?",
                (doc_id, farmer_id),
            ).fetchone()
        finally:
            conn.close()
    if row is None or not os.path.isfile(row["stored_path"]):
        raise PortalError("Document not found.", 404, "NOT_FOUND")
    return row["stored_path"], row["mime"], row["original_name"]


def delete_document(farmer_id: str, doc_id: str) -> None:
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT stored_path, title FROM portal_documents WHERE id = ? AND farmer_id = ?", (doc_id, farmer_id),
            ).fetchone()
            if row is None:
                raise PortalError("Document not found.", 404, "NOT_FOUND")
            conn.execute("DELETE FROM portal_documents WHERE id = ?", (doc_id,))
            _activity(conn, farmer_id, "DOCUMENT_REMOVED", f"Removed document: {row['title']}")
            conn.commit()
        finally:
            conn.close()
    if os.path.isfile(row["stored_path"]):
        os.remove(row["stored_path"])


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

# Evergreen guidance, deliberately without dates or amounts that go stale.
_BROADCASTS = (
    ("broadcast:no-otp", "ALERT", "WARNING", "Never share your password or OTP",
     "AI Krishi staff will never ask for your password, OTP, UPI PIN or bank details on a call, SMS or "
     "WhatsApp message. Do not share them with anyone, even if the caller says they are from the government.",
     "AI Krishi"),
    ("broadcast:pmkisan-ekyc", "SCHEME", "INFO", "PM-KISAN: keep your e-KYC complete",
     "PM-KISAN instalments are paid only to beneficiaries whose e-KYC is complete and whose bank account is "
     "seeded with Aadhaar. You can complete e-KYC on the PM-KISAN portal with an OTP, or with biometrics at a "
     "Common Service Centre.",
     "AI Krishi information desk, summarised from PM-KISAN guidelines"),
    ("broadcast:pmfby", "SCHEME", "INFO", "Crop insurance under PMFBY",
     "Pradhan Mantri Fasal Bima Yojana covers notified crops each Kharif and Rabi season. Enrolment cut-off "
     "dates are notified by the state for every crop, so confirm the date with your bank, primary agricultural "
     "cooperative society or Common Service Centre before the sowing window closes.",
     "AI Krishi information desk, summarised from PMFBY guidelines"),
    ("broadcast:soil-health-card", "GOVERNMENT", "INFO", "Get your soil tested",
     "A Soil Health Card shows the nutrient status of your field and the fertiliser doses recommended for it. "
     "Contact your nearest Raitha Samparka Kendra or agriculture department office to submit a soil sample.",
     "AI Krishi information desk"),
)


def _ensure_broadcasts(conn: sqlite3.Connection) -> None:
    for key, category, severity, title, body, source in _BROADCASTS:
        _notify(conn, None, category, title, body, severity=severity, source=source, dedupe_key=key)


_WEATHER_CHECK_SECONDS = 3600
_weather_checked: Dict[str, int] = {}


def weather_check_due(farmer_id: str) -> bool:
    return _now() - _weather_checked.get(farmer_id, 0) >= _WEATHER_CHECK_SECONDS


def record_weather(farmer_id: str, district: str, weather: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a live weather reading into dated alerts. Fallback readings raise nothing."""
    _weather_checked[farmer_id] = _now()
    live = bool(weather) and weather.get("source") != "fallback"
    reading = {
        "live": live,
        "district": district,
        "temperature": weather.get("temperature") if weather else None,
        "humidity": weather.get("humidity") if weather else None,
        "rainfall_mm": weather.get("rainfall") if weather else None,
        "wind_kmh": round(float(weather.get("wind_speed") or 0) * 3.6) if weather else None,
        "description": weather.get("description") if weather else None,
        "checked_at": _now(),
        "source": "OpenWeather" if live else "Unavailable",
    }
    if not live:
        return reading

    t = float(weather.get("temperature") or 0)
    h = float(weather.get("humidity") or 0)
    rain = float(weather.get("rainfall") or 0)
    wind = reading["wind_kmh"] or 0
    alerts = []
    if t >= 38:
        alerts.append(("heat", "WARNING", f"Heat stress advisory for {district}",
                       f"The temperature is {t:.0f}°C. Irrigate in the early morning or evening, mulch to hold soil "
                       "moisture, and avoid spraying pesticides in the afternoon heat."))
    if t <= 8:
        alerts.append(("cold", "WARNING", f"Cold conditions in {district}",
                       f"The temperature has dropped to {t:.0f}°C. Give a light irrigation in the evening to reduce "
                       "frost injury and cover nursery beds overnight."))
    if rain >= 7.5:
        alerts.append(("rain", "WARNING", f"Heavy rain in {district}",
                       f"{rain:.1f} mm of rain fell in the last hour. Clear field drainage channels, postpone fertiliser "
                       "and pesticide application, and keep harvested produce under cover."))
    if wind >= 40:
        alerts.append(("wind", "WARNING", f"Strong winds in {district}",
                       f"Winds of about {wind} km/h. Stake tall crops, secure shade nets and polyhouse covers, and "
                       "avoid spraying until the wind drops."))
    if h >= 85 and 18 <= t <= 32:
        alerts.append(("humidity", "INFO", "High humidity: watch for fungal disease",
                       f"Humidity is {h:.0f}%. These conditions favour fungal diseases such as blight and mildew. "
                       "Inspect leaves closely, avoid overhead irrigation and improve air flow between plants."))

    if alerts:
        stamp = datetime.now(IST)
        with DB_LOCK:
            conn = _connect()
            try:
                for kind, severity, title, body in alerts:
                    _notify(conn, farmer_id, "WEATHER", title, body, severity=severity,
                            source=f"OpenWeather, {district}, {stamp.strftime('%d %b %Y %H:%M')} IST",
                            dedupe_key=f"weather:{farmer_id}:{kind}:{stamp.date().isoformat()}",
                            expires_at=_now() + 24 * 3600)
                conn.commit()
            finally:
                conn.close()
    return reading


def _visible_notifications(conn: sqlite3.Connection, farmer_id: str, category: Optional[str] = None,
                           limit: int = 100) -> List[Dict[str, Any]]:
    _ensure_broadcasts(conn)
    conn.commit()
    where = "(n.farmer_id = ? OR n.farmer_id IS NULL) AND (n.expires_at IS NULL OR n.expires_at > ?)"
    params: List[Any] = [farmer_id, farmer_id, _now()]
    if category:
        where += " AND n.category = ?"
        params.append(category)
    rows = conn.execute(
        f"""SELECT n.*, r.read_at FROM portal_notifications n
            LEFT JOIN portal_notification_reads r ON r.notification_id = n.id AND r.farmer_id = ?
            WHERE {where} ORDER BY n.created_at DESC LIMIT ?""",
        (*params, limit),
    ).fetchall()
    return [{
        "id": r["id"], "category": r["category"],
        "category_label": NOTIFICATION_CATEGORIES.get(r["category"], r["category"]),
        "severity": r["severity"], "title": r["title"], "body": r["body"], "source": r["source"],
        "link": r["link"], "created_at": r["created_at"], "read": r["read_at"] is not None,
        "personal": r["farmer_id"] is not None,
    } for r in rows]


def list_notifications(farmer_id: str, category: Optional[str] = None) -> Dict[str, Any]:
    if category and category not in NOTIFICATION_CATEGORIES:
        raise PortalError("Unknown notification category.", 422, "VALIDATION")
    with DB_LOCK:
        conn = _connect()
        try:
            _load(conn, farmer_id)
            items = _visible_notifications(conn, farmer_id, category)
            everything = items if not category else _visible_notifications(conn, farmer_id)
        finally:
            conn.close()
    counts = {k: 0 for k in NOTIFICATION_CATEGORIES}
    for n in everything:
        if not n["read"]:
            counts[n["category"]] = counts.get(n["category"], 0) + 1
    return {"items": items, "unread": sum(counts.values()), "unread_by_category": counts}


def unread_count(farmer_id: str) -> int:
    return list_notifications(farmer_id)["unread"]


def mark_read(farmer_id: str, notification_id: Optional[str] = None) -> int:
    with DB_LOCK:
        conn = _connect()
        try:
            visible = _visible_notifications(conn, farmer_id, limit=1000)
            targets = [n["id"] for n in visible if not n["read"] and (notification_id is None or n["id"] == notification_id)]
            if notification_id and notification_id not in {n["id"] for n in visible}:
                raise PortalError("Notification not found.", 404, "NOT_FOUND")
            conn.executemany(
                "INSERT OR IGNORE INTO portal_notification_reads(notification_id, farmer_id, read_at) VALUES (?,?,?)",
                [(nid, farmer_id, _now()) for nid in targets],
            )
            conn.commit()
            return len(targets)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Activity and overview
# ---------------------------------------------------------------------------

def log_call_request(farmer_id: str, call_sid: str, to_number: str) -> None:
    with DB_LOCK:
        conn = _connect()
        try:
            _activity(conn, farmer_id, "CALL_REQUESTED",
                      f"Requested a call from the AI Krishi assistant to {to_number} ({call_sid})")
            conn.commit()
        finally:
            conn.close()


def recent_activity(farmer_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = _connect()
        try:
            farmer = _load(conn, farmer_id)
            items = [{
                "kind": "portal", "action": r["action"], "detail": r["detail"], "at": r["created_at"],
            } for r in conn.execute(
                "SELECT action, detail, created_at FROM portal_activity WHERE farmer_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (farmer_id, limit),
            )]
            try:
                for c in conn.execute(
                    """SELECT v.call_sid, v.started_at, v.turn_count, v.language,
                              (SELECT farmer_en FROM voice_turns t WHERE t.call_sid = v.call_sid ORDER BY turn_no DESC LIMIT 1) AS last_question
                       FROM voice_calls v WHERE v.phone = ? ORDER BY v.started_at DESC LIMIT ?""",
                    (farmer["mobile"], limit),
                ):
                    question = c["last_question"]
                    items.append({
                        "kind": "call", "action": "HELPLINE_CALL",
                        "detail": f"Helpline call, {c['turn_count']} question{'s' if c['turn_count'] != 1 else ''}"
                                  + (f": “{question}”" if question else ""),
                        "at": c["started_at"],
                    })
            except sqlite3.OperationalError:
                pass  # voice tables are created by the call handler on first use
        finally:
            conn.close()
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]


def overview(farmer_id: str) -> Dict[str, Any]:
    profile = get_profile(farmer_id)
    farm = farm_summary(farmer_id)
    crops = list_crops(farmer_id)
    documents = list_documents(farmer_id)
    notes = list_notifications(farmer_id)
    current = [c for c in crops if c["is_current"]]
    year_start = f"{_today().year}-01-01"
    checklist = [
        {"key": "password", "label": "Set your own password", "done": not profile["must_change_password"], "link": "/portal/profile#security"},
        {"key": "photo", "label": "Add a profile photograph", "done": profile["photo_available"], "link": "/portal/profile"},
        {"key": "personal", "label": "Add date of birth and guardian name", "done": bool(profile["date_of_birth"] and profile["guardian_name"]), "link": "/portal/profile"},
        {"key": "address", "label": "Add PIN code to your address", "done": bool(profile["pincode"]), "link": "/portal/profile"},
        {"key": "plots", "label": "Record your land plots", "done": farm["totals"]["plots"] > 0, "link": "/portal/farm"},
        {"key": "land_record", "label": "Upload a land record (RTC / Pahani)", "done": any(d["category"] == "LAND_RECORD" for d in documents["uploaded"]), "link": "/portal/documents"},
    ]
    return {
        "farmer": profile,
        "stats": {
            "total_acres": farm["totals"]["total_acres"] or profile["declared_land_acres"] or 0,
            "land_source": "plots" if farm["totals"]["plots"] else "declared",
            "plots": farm["totals"]["plots"],
            "current_crops": len(current),
            "area_under_crops": round(sum(c["area_acres"] for c in current), 3),
            "harvests_this_year": sum(1 for c in crops for h in c["harvests"] if h["harvest_date"] >= year_start),
            "documents": len(documents["uploaded"]) + len(documents["issued"]),
            "unread_notifications": notes["unread"],
        },
        "current_crops": current[:6],
        "recent_activity": recent_activity(farmer_id, 8),
        "notifications": notes["items"][:4],
        "checklist": checklist,
        "checklist_done": sum(1 for c in checklist if c["done"]),
    }


# ---------------------------------------------------------------------------
# ID card
# ---------------------------------------------------------------------------

def _qr_svg(payload: str) -> str:
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0, image_factory=SvgPathImage)
    qr.add_data(payload)
    qr.make(fit=True)
    svg = qr.make_image().to_string(encoding="unicode")
    svg = re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
    return re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)


def verification_code(farmer_id: str, serial: str) -> str:
    token = auth.card_token(farmer_id, serial).upper()
    return f"{token[:4]}-{token[4:8]}-{token[8:12]}"


def card(farmer_id: str, base_url: str) -> Dict[str, Any]:
    from app.config import get_settings

    with DB_LOCK:
        conn = _connect()
        try:
            row = _load(conn, farmer_id)
            plots = conn.execute(
                "SELECT survey_number, area_acres, irrigation_type FROM portal_plots WHERE farmer_id = ? ORDER BY created_at",
                (farmer_id,),
            ).fetchall()
        finally:
            conn.close()

    token = auth.card_token(row["farmer_id"], row["card_serial"])
    verify_url = f"{base_url.rstrip('/')}/verify/{row['farmer_id']}?t={token}"
    status, label = card_status(row)
    plot_area = round(sum(p["area_acres"] for p in plots), 2)
    address = ", ".join(x for x in (row["address_line"], row["village"], row["taluk"] and f"{row['taluk']} Taluk",
                                     row["district"] and f"{row['district']} District", row["state"],
                                     row["pincode"]) if x)
    return {
        "farmer_id": row["farmer_id"],
        "card_serial": row["card_serial"],
        "full_name": row["full_name"],
        "guardian_name": row["guardian_name"],
        "date_of_birth": row["date_of_birth"],
        "gender": row["gender"],
        "mobile": row["mobile"],
        "village": row["village"], "taluk": row["taluk"], "district": row["district"],
        "state": row["state"], "pincode": row["pincode"],
        "address": address,
        "land": {
            "total_acres": plot_area if plots else row["declared_land_acres"],
            "basis": "recorded plots" if plots else ("declared at registration" if row["declared_land_acres"] else None),
            "plots": len(plots),
            "survey_numbers": [p["survey_number"] for p in plots if p["survey_number"]][:4],
            "irrigation": sorted({p["irrigation_type"] for p in plots if p["irrigation_type"]}),
        },
        "registered_on": _date_label(row["registered_at"]),
        "valid_until": _date_label(row["valid_until"]),
        "verified_on": _date_label(row["verified_at"]),
        "status": status,
        "status_label": label,
        "photo_available": bool(row["photo_path"]),
        "photo_version": row["updated_at"],
        "verification_code": verification_code(row["farmer_id"], row["card_serial"]),
        "verify_url": verify_url,
        "qr_svg": _qr_svg(verify_url),
        "issuer": "AI Krishi Farmer Registry",
        "helpline": get_settings().TWILIO_PHONE_NUMBER or None,
    }


def verify_card(farmer_id: str, token: str) -> Dict[str, Any]:
    farmer_id = auth.canonical_farmer_id(farmer_id)
    not_verified = PortalError(
        "This card could not be verified. The QR code may be damaged, altered or not issued by the AI Krishi Farmer Registry.",
        404, "NOT_VERIFIED",
    )
    if not auth.farmer_id_is_well_formed(farmer_id):
        raise not_verified
    with DB_LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM portal_farmers WHERE farmer_id = ?", (farmer_id,)).fetchone()
        finally:
            conn.close()
    if row is None or not auth.card_token_valid(row["farmer_id"], row["card_serial"], token):
        raise not_verified
    status, label = card_status(row)
    digits = re.sub(r"\D", "", row["mobile"])[-10:]
    return {
        "farmer_id": row["farmer_id"],
        "full_name": row["full_name"],
        "mobile_masked": f"+91 {digits[:2]}XXX XX{digits[-3:]}",
        "village": row["village"], "district": row["district"], "state": row["state"],
        "status": status, "status_label": label,
        "registered_on": _date_label(row["registered_at"]),
        "valid_until": _date_label(row["valid_until"]),
        "verified_on": _date_label(row["verified_at"]),
        "card_serial": row["card_serial"],
        "photo_available": bool(row["photo_path"]),
        "checked_at": _now(),
        "issuer": "AI Krishi Farmer Registry",
    }


def verified_photo(farmer_id: str, token: str) -> Optional[Tuple[str, str]]:
    verify_card(farmer_id, token)
    return photo_file(auth.canonical_farmer_id(farmer_id))


def meta() -> Dict[str, Any]:
    from app.config import get_settings

    return {
        "helpline": get_settings().TWILIO_PHONE_NUMBER or None,
        "call_number": get_settings().PORTAL_CALL_NUMBER or None,
        "states": list(auth.STATE_CODES),
        "genders": list(GENDERS),
        "languages": LANGUAGES,
        "ownership": OWNERSHIP,
        "soil_types": list(SOIL_TYPES),
        "irrigation_types": list(IRRIGATION_TYPES),
        "seasons": list(SEASONS),
        "crop_statuses": CROP_STATUS,
        "crop_names": crop_names(),
        "harvest_units": list(HARVEST_UNITS),
        "quality_grades": list(QUALITY_GRADES),
        "document_categories": DOCUMENT_CATEGORIES,
        "notification_categories": NOTIFICATION_CATEGORIES,
        "limits": {"photo_bytes": MAX_PHOTO_BYTES, "document_bytes": MAX_DOCUMENT_BYTES},
    }
