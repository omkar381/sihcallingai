"""
Canonical commodity, market, unit and quantity normalisation.

Agricultural feeds and farmers do not agree on names. data.gov.in says
"Onion", a scraped table says "ONION (RED)", a Kannada caller says
"ಈರುಳ್ಳಿ" and a Marathi caller says "कांदा". All four must resolve to one
canonical commodity or price history fragments into unusable slivers.

Matching is deliberately conservative: an unrecognised term returns no match
rather than the nearest guess, because silently mapping "Bengal Gram" onto
"Green Gram" would corrupt every downstream price series.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# Indian mandi prices are quoted per quintal (100 kg). That is the canonical
# internal unit; everything converts to it so comparisons are always valid.

KG_PER_QUINTAL = 100.0
KG_PER_TONNE = 1000.0

_UNIT_ALIASES: Dict[str, str] = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg", "ಕೆಜಿ": "kg", "किलो": "kg",
    "q": "quintal", "qtl": "quintal", "quintal": "quintal",
    "quintals": "quintal", "ಕ್ವಿಂಟಲ್": "quintal", "क्विंटल": "quintal",
    "ton": "tonne", "tons": "tonne", "tonne": "tonne", "tonnes": "tonne",
    "mt": "tonne", "ಟನ್": "tonne", "टन": "tonne",
    "bag": "bag", "bags": "bag", "ಚೀಲ": "bag", "पोते": "bag",
}

# A "bag" is not a unit of mass, but farmers speak in bags. 50kg is the
# dominant sack size for onion/grain in Karnataka and Maharashtra; anything
# derived from it is flagged as an assumption by the caller.
ASSUMED_KG_PER_BAG = 50.0

_UNIT_TO_KG: Dict[str, float] = {
    "kg": 1.0,
    "quintal": KG_PER_QUINTAL,
    "tonne": KG_PER_TONNE,
    "bag": ASSUMED_KG_PER_BAG,
}


def normalize_unit(text: Optional[str]) -> Optional[str]:
    """Resolve a spoken or written unit to kg / quintal / tonne / bag."""
    if not text:
        return None
    key = str(text).strip().lower().rstrip(".")
    return _UNIT_ALIASES.get(key)


def to_kg(quantity: float, unit: str) -> Optional[float]:
    """Convert a quantity to kilograms. None when the unit is unknown."""
    canonical = normalize_unit(unit)
    if canonical is None:
        return None
    return float(quantity) * _UNIT_TO_KG[canonical]


def price_per_quintal_to_per_kg(price: float) -> float:
    return float(price) / KG_PER_QUINTAL


def price_per_kg_to_per_quintal(price: float) -> float:
    return float(price) * KG_PER_QUINTAL


# ---------------------------------------------------------------------------
# Commodities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Commodity:
    canonical: str
    group: str
    perishable: bool
    # Days the crop can typically be held by a smallholder without cold
    # storage. Feeds the sale-window engine: advising a farmer to wait
    # twelve days is only honest if the crop survives twelve days.
    typical_storage_days: int
    aliases: Tuple[str, ...]


COMMODITY_MASTER: Tuple[Commodity, ...] = (
    Commodity("Onion", "vegetable", True, 45, (
        "onion", "onions", "red onion", "白onion", "shallot", "kanda",
        "ಈರುಳ್ಳಿ", "ಉಳ್ಳಾಗಡ್ಡೆ", "कांदा", "प्याज", "pyaz",
    )),
    Commodity("Tomato", "vegetable", True, 7, (
        "tomato", "tomatoes", "ಟೊಮೆಟೊ", "ಟೊಮ್ಯಾಟೊ", "टोमॅटो", "टमाटर", "tamatar",
    )),
    Commodity("Potato", "vegetable", True, 90, (
        "potato", "potatoes", "ಆಲೂಗಡ್ಡೆ", "बटाटा", "आलू", "aloo", "batata",
    )),
    Commodity("Ragi", "cereal", False, 365, (
        "ragi", "finger millet", "fingermillet", "nachni",
        "ರಾಗಿ", "नाचणी", "मडुआ",
    )),
    Commodity("Maize", "cereal", False, 180, (
        "maize", "corn", "makka", "ಮೆಕ್ಕೆಜೋಳ", "ಜೋಳ", "मका", "मक्का",
    )),
    Commodity("Wheat", "cereal", False, 365, (
        "wheat", "gehun", "ಗೋಧಿ", "गहू", "गेहूं",
    )),
    Commodity("Paddy", "cereal", False, 365, (
        "paddy", "rice", "dhan", "ಭತ್ತ", "ಅಕ್ಕಿ", "भात", "धान",
    )),
    Commodity("Jowar", "cereal", False, 365, (
        "jowar", "sorghum", "ಬಿಳಿಜೋಳ", "ज्वारी", "ज्वार",
    )),
    Commodity("Bajra", "cereal", False, 365, (
        "bajra", "pearl millet", "ಸಜ್ಜೆ", "बाजरी", "बाजरा",
    )),
    Commodity("Cotton", "fibre", False, 365, (
        "cotton", "kapas", "ಹತ್ತಿ", "कापूस", "कपास",
    )),
    Commodity("Sugarcane", "cash", True, 3, (
        "sugarcane", "cane", "ಕಬ್ಬು", "ऊस", "गन्ना",
    )),
    Commodity("Chilli", "spice", False, 180, (
        "chilli", "chilly", "chillies", "red chilli", "dry chilli", "mirchi",
        "ಮೆಣಸಿನಕಾಯಿ", "ಒಣ ಮೆಣಸಿನಕಾಯಿ", "मिरची", "मिर्च",
    )),
    Commodity("Turmeric", "spice", False, 365, (
        "turmeric", "haldi", "ಅರಿಶಿನ", "हळद", "हल्दी",
    )),
    Commodity("Groundnut", "oilseed", False, 180, (
        "groundnut", "peanut", "ground nut", "ಕಡಲೆಕಾಯಿ", "शेंगदाणा", "मूंगफली",
    )),
    Commodity("Soyabean", "oilseed", False, 180, (
        "soyabean", "soybean", "soya bean", "soya", "ಸೋಯಾಬೀನ್", "सोयाबीन",
    )),
    Commodity("Tur", "pulse", False, 365, (
        "tur", "arhar", "pigeon pea", "red gram", "toor", "toor dal",
        "ತೊಗರಿ", "तूर", "अरहर",
    )),
    Commodity("Bengal Gram", "pulse", False, 365, (
        "bengal gram", "chana", "chickpea", "gram", "ಕಡಲೆ", "हरभरा", "चना",
    )),
    Commodity("Green Gram", "pulse", False, 365, (
        "green gram", "moong", "mung", "ಹೆಸರುಕಾಳು", "मूग", "मूंग",
    )),
    Commodity("Black Gram", "pulse", False, 365, (
        "black gram", "urad", "urd", "ಉದ್ದಿನಕಾಳು", "उडीद", "उड़द",
    )),
    Commodity("Banana", "fruit", True, 7, (
        "banana", "bananas", "ಬಾಳೆಹಣ್ಣು", "केळी", "केला",
    )),
    Commodity("Grapes", "fruit", True, 14, (
        "grapes", "grape", "ದ್ರಾಕ್ಷಿ", "द्राक्ष", "अंगूर",
    )),
    Commodity("Pomegranate", "fruit", True, 30, (
        "pomegranate", "anar", "ದಾಳಿಂಬೆ", "डाळिंब", "अनार",
    )),
    Commodity("Ginger", "spice", True, 60, (
        "ginger", "adrak", "ಶುಂಠಿ", "आले", "अदरक",
    )),
    Commodity("Garlic", "spice", False, 120, (
        "garlic", "lehsun", "ಬೆಳ್ಳುಳ್ಳಿ", "लसूण", "लहसुन",
    )),
)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    """
    Lowercase, de-punctuate and collapse whitespace for alias matching.

    Decimal points between digits are preserved: stripping them turns
    "2.5 quintal" into "2 5 quintal", which parses as 2 quintal and
    understates the farmer's lot by 20%.
    """
    lowered = str(text).strip().lower()
    lowered = _strip_accents(lowered)
    lowered = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", lowered)      # periods, except decimals
    lowered = re.sub(r"[()\[\]{}/_,\-]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


# Alias -> canonical, built once. Longer aliases are checked first so
# "green gram" wins over "gram".
_ALIAS_INDEX: Dict[str, Commodity] = {}
for _c in COMMODITY_MASTER:
    _ALIAS_INDEX[_slug(_c.canonical)] = _c
    for _a in _c.aliases:
        _ALIAS_INDEX.setdefault(_slug(_a), _c)

_ALIASES_BY_LENGTH: List[str] = sorted(_ALIAS_INDEX, key=len, reverse=True)

COMMODITY_BY_CANONICAL: Dict[str, Commodity] = {c.canonical: c for c in COMMODITY_MASTER}


@dataclass
class CommodityMatch:
    commodity: Commodity
    matched_alias: str
    exact: bool

    @property
    def canonical(self) -> str:
        return self.commodity.canonical


def resolve_commodity(text: Optional[str]) -> Optional[CommodityMatch]:
    """
    Resolve free text to a canonical commodity, or None.

    Tries an exact alias hit first, then looks for an alias appearing as a
    whole phrase inside a longer utterance ("price of red onion today").
    Returns None rather than guessing - an unknown crop must surface as
    unknown, never as the closest-looking one.
    """
    if not text:
        return None

    slug = _slug(text)
    if not slug:
        return None

    direct = _ALIAS_INDEX.get(slug)
    if direct is not None:
        return CommodityMatch(direct, slug, exact=True)

    padded = f" {slug} "
    for alias in _ALIASES_BY_LENGTH:
        if len(alias) < 3:
            continue
        if f" {alias} " in padded:
            return CommodityMatch(_ALIAS_INDEX[alias], alias, exact=False)

    return None


def canonical_commodity(text: Optional[str]) -> Optional[str]:
    match = resolve_commodity(text)
    return match.canonical if match else None


# ---------------------------------------------------------------------------
# Markets and administrative names
# ---------------------------------------------------------------------------

# Feeds spell the same mandi several ways; district names drift too.
# Only genuine spelling variants belong here, never two distinct markets.
_MARKET_ALIASES: Dict[str, str] = {
    "gulbarga": "Kalaburagi",
    "kalaburgi": "Kalaburagi",
    "kalburgi": "Kalaburagi",
    "kalaburagi": "Kalaburagi",
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "mysore": "Mysuru",
    "mysuru": "Mysuru",
    "belgaum": "Belagavi",
    "belagavi": "Belagavi",
    "hubli": "Hubballi",
    "hubballi": "Hubballi",
    "bijapur": "Vijayapura",
    "vijayapura": "Vijayapura",
    "bellary": "Ballari",
    "ballari": "Ballari",
    "shimoga": "Shivamogga",
    "shivamogga": "Shivamogga",
    "tumkur": "Tumakuru",
    "tumakuru": "Tumakuru",
    "bombay": "Mumbai",
    "mumbai": "Mumbai",
    "poona": "Pune",
    "pune": "Pune",
}

# Longest first, and stripped repeatedly: "Lasalgaon Market Yard" must lose
# both words, not just the trailing one.
_MARKET_SUFFIXES = tuple(sorted(
    (
        " apmc", " a p m c", " market", " mandi", " yard", " market yard",
        " agricultural produce market committee", " apmc yard", " krushi utpanna bazar samiti",
    ),
    key=len,
    reverse=True,
))


def normalize_market_name(text: Optional[str]) -> Optional[str]:
    """
    Canonicalise a market or district name.

    Strips APMC/mandi decorations, applies the rename map (Gulbarga is
    Kalaburagi), and title-cases the remainder. Unknown names are kept as
    written rather than discarded.
    """
    if not text:
        return None

    slug = _slug(text)
    if not slug:
        return None

    stripped = True
    while stripped and slug:
        stripped = False
        for suffix in _MARKET_SUFFIXES:
            if slug.endswith(suffix):
                slug = slug[: -len(suffix)].strip()
                stripped = True
                break

    if not slug:
        return None

    if slug in _MARKET_ALIASES:
        return _MARKET_ALIASES[slug]

    return " ".join(part.capitalize() for part in slug.split())


def normalize_state_name(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    slug = _slug(text)
    return " ".join(part.capitalize() for part in slug.split()) if slug else None


# ---------------------------------------------------------------------------
# Grades
# ---------------------------------------------------------------------------

_GRADE_ALIASES: Dict[str, str] = {
    "a": "A", "grade a": "A", "fair average quality": "FAQ", "faq": "FAQ",
    "b": "B", "grade b": "B", "c": "C", "grade c": "C",
    "large": "A", "medium": "B", "small": "C",
    "local": "LOCAL", "other": "OTHER", "non faq": "NON_FAQ",
}


def normalize_grade(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    slug = _slug(text)
    if not slug:
        return None
    return _GRADE_ALIASES.get(slug, slug.upper().replace(" ", "_"))


# ---------------------------------------------------------------------------
# Spoken quantities
# ---------------------------------------------------------------------------

_SPOKEN_NUMBERS = {
    "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twenty": 20, "fifty": 50, "hundred": 100, "thousand": 1000,
}


@dataclass
class ParsedQuantity:
    value: float
    unit: str
    kg: float
    assumed: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "value": self.value,
            "unit": self.unit,
            "kg": round(self.kg, 3),
            "assumed_bag_weight": self.assumed,
        }


def parse_quantity(text: Optional[str]) -> Optional[ParsedQuantity]:
    """
    Parse "50 kg", "2 quintal", "half ton", "10 bags" into kilograms.

    Returns None when no quantity is present, so callers can ask the farmer
    instead of assuming a default.
    """
    if not text:
        return None

    slug = _slug(text)
    if not slug:
        return None

    unit = None
    for token in sorted(_UNIT_ALIASES, key=len, reverse=True):
        if re.search(rf"(?:^|\s|\d){re.escape(token)}(?:\s|$)", slug):
            unit = _UNIT_ALIASES[token]
            break
    if unit is None:
        return None

    number: Optional[float] = None
    numeric = re.search(r"(\d+(?:\.\d+)?)", slug)
    if numeric:
        number = float(numeric.group(1))
    else:
        for word, value in _SPOKEN_NUMBERS.items():
            if re.search(rf"(?:^|\s){word}(?:\s|$)", slug):
                number = float(value)
                break

    if number is None or number <= 0:
        return None

    kg = to_kg(number, unit)
    if kg is None:
        return None

    return ParsedQuantity(value=number, unit=unit, kg=kg, assumed=(unit == "bag"))
