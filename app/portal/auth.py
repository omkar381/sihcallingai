"""
Credentials and identifiers for the farmer portal.

Passwords are PBKDF2-SHA256 with a per-password salt. Session tokens are
random and only their SHA-256 is stored, so a leaked database cannot be
replayed as a login. The QR code on the ID card carries an HMAC so the public
verification page cannot be used to enumerate farmers by guessing IDs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import List, Optional, Tuple

from app.config import get_settings

PBKDF2_ITERATIONS = 390_000

SESSION_COOKIE = "akr_portal"
SESSION_SECONDS = 12 * 3600
CLIENT_HEADER = "X-Portal-Client"

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60

# No 0/O, 1/l/I: the password is read aloud and copied off a screen.
_TEMP_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_TEMP_LOWER = "abcdefghijkmnpqrstuvwxyz"
_TEMP_DIGITS = "23456789"
_TEMP_SYMBOLS = "@#$%&*"

STATE_CODES = {
    "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR", "Assam": "AS", "Bihar": "BR",
    "Chhattisgarh": "CG", "Goa": "GA", "Gujarat": "GJ", "Haryana": "HR",
    "Himachal Pradesh": "HP", "Jharkhand": "JH", "Karnataka": "KA", "Kerala": "KL",
    "Madhya Pradesh": "MP", "Maharashtra": "MH", "Manipur": "MN", "Meghalaya": "ML",
    "Mizoram": "MZ", "Nagaland": "NL", "Odisha": "OD", "Punjab": "PB", "Rajasthan": "RJ",
    "Sikkim": "SK", "Tamil Nadu": "TN", "Telangana": "TG", "Tripura": "TR",
    "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB",
    "Andaman and Nicobar Islands": "AN", "Chandigarh": "CH",
    "Dadra and Nagar Haveli and Daman and Diu": "DH", "Delhi": "DL",
    "Jammu and Kashmir": "JK", "Ladakh": "LA", "Lakshadweep": "LD", "Puducherry": "PY",
}

_DISTRICT_CODES = {
    "bagalkote": "BGK", "bagalkot": "BGK", "ballari": "BLY", "bellary": "BLY",
    "belagavi": "BGM", "belgaum": "BGM", "bengaluru rural": "BGR", "bengaluru urban": "BLR",
    "bengaluru": "BLR", "bangalore": "BLR", "bidar": "BDR", "chamarajanagar": "CMN",
    "chikkaballapur": "CBP", "chikkamagaluru": "CKM", "chikmagalur": "CKM",
    "chitradurga": "CTA", "dakshina kannada": "DKA", "davanagere": "DVG", "dharwad": "DWD",
    "gadag": "GDG", "hassan": "HSN", "haveri": "HVR", "kalaburagi": "KLB",
    "kalaburgi": "KLB", "kalburgi": "KLB", "gulbarga": "KLB", "kodagu": "KDG",
    "kolar": "KLR", "koppal": "KPL", "mandya": "MDY", "mysuru": "MYS", "mysore": "MYS",
    "raichur": "RCR", "ramanagara": "RMN", "shivamogga": "SMG", "shimoga": "SMG",
    "tumakuru": "TMK", "tumkur": "TMK", "udupi": "UDP", "uttara kannada": "UKA",
    "vijayanagara": "VJN", "vijayapura": "VJP", "bijapur": "VJP", "yadgir": "YDG",
}


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


def generate_temporary_password(length: int = 12) -> str:
    pools = (_TEMP_UPPER, _TEMP_LOWER, _TEMP_DIGITS, _TEMP_SYMBOLS)
    chars = [secrets.choice(pool) for pool in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def password_problems(password: str, *, mobile: str = "", login_id: str = "") -> List[str]:
    """Why a new password is not acceptable. Empty when it is fine."""
    problems = []
    password = password or ""
    if len(password) < 8:
        problems.append("Use at least 8 characters.")
    if len(password) > 128:
        problems.append("Use at most 128 characters.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("Include at least one letter and one number.")
    digits = re.sub(r"\D", "", mobile)[-10:]
    if digits and digits in password:
        problems.append("Do not use your mobile number in the password.")
    if login_id and login_id.split(".")[0].lower() in password.lower() and len(login_id.split(".")[0]) >= 4:
        problems.append("Do not use your login ID in the password.")
    return problems


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def state_code(state: str) -> str:
    for name, code in STATE_CODES.items():
        if name.lower() == (state or "").strip().lower():
            return code
    letters = re.sub(r"[^A-Za-z]", "", state or "").upper()
    return (letters[:2] or "XX").ljust(2, "X")


def district_code(district: str) -> str:
    key = re.sub(r"\s+", " ", (district or "").strip().lower())
    if key in _DISTRICT_CODES:
        return _DISTRICT_CODES[key]
    letters = re.sub(r"[^A-Za-z]", "", district or "").upper()
    if not letters:
        return "XXX"
    consonants = [c for c in letters[1:] if c not in "AEIOU"]
    return (letters[0] + "".join(consonants[:2])).ljust(3, "X")


def luhn_check_digit(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def format_farmer_id(state: str, district: str, year: int, sequence: int) -> str:
    """KA-KLB-26-00001-7: state, district, registration year, sequence, check digit."""
    yy = f"{year % 100:02d}"
    seq = f"{sequence:05d}"
    return f"{state_code(state)}-{district_code(district)}-{yy}-{seq}-{luhn_check_digit(yy + seq)}"


def farmer_id_is_well_formed(farmer_id: str) -> bool:
    m = re.fullmatch(r"[A-Z]{2}-[A-Z]{3}-(\d{2})-(\d{5})-(\d)", farmer_id or "")
    return bool(m) and luhn_check_digit(m.group(1) + m.group(2)) == m.group(3)


def canonical_farmer_id(value: str) -> str:
    """Accept 'ka klb 26 00001 7' or 'KAKLB26000017' as typed on a phone keypad."""
    compact = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    m = re.fullmatch(r"([A-Z]{2})([A-Z]{3})(\d{2})(\d{5})(\d)", compact)
    if not m:
        return (value or "").strip().upper()
    return "-".join(m.groups())


def login_id_base(full_name: str, mobile: str) -> str:
    first = re.sub(r"[^a-z]", "", (full_name or "").strip().split(" ")[0].lower())[:12]
    if len(first) < 3:
        first = "farmer"
    return f"{first}.{re.sub(r'[^0-9]', '', mobile)[-4:]}"


def card_serial(year: int) -> str:
    return f"AKR/{year}/{secrets.token_hex(4).upper()}"


def normalise_mobile(value: str) -> Optional[str]:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"[6-9]\d{9}", digits):
        return f"+91{digits}"
    return None


# ---------------------------------------------------------------------------
# Sessions and signed card links
# ---------------------------------------------------------------------------

def new_session_token() -> Tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _portal_secret() -> bytes:
    configured = (getattr(get_settings(), "PORTAL_SECRET", "") or "").strip()
    if configured:
        return configured.encode("utf-8")
    from app.security import get_api_key

    return hashlib.sha256(b"akr-portal-card|" + get_api_key().encode("utf-8")).digest()


def card_token(farmer_id: str, serial: str) -> str:
    mac = hmac.new(_portal_secret(), f"{farmer_id}|{serial}".encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:24]


def card_token_valid(farmer_id: str, serial: str, token: str) -> bool:
    return hmac.compare_digest(card_token(farmer_id, serial), (token or "").strip().lower())
