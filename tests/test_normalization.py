"""
Commodity, market, unit and quantity normalisation.

A wrong mapping here silently corrupts every price series built on top of it,
so the tests weight false positives (mapping an unknown crop onto a known one)
as heavily as false negatives.
"""

from __future__ import annotations

import pytest

from app.market.normalization import (
    ASSUMED_KG_PER_BAG,
    canonical_commodity,
    normalize_grade,
    normalize_market_name,
    normalize_unit,
    parse_quantity,
    price_per_kg_to_per_quintal,
    price_per_quintal_to_per_kg,
    resolve_commodity,
    to_kg,
)


class TestCommodityResolution:
    @pytest.mark.parametrize(
        "text",
        ["Onion", "onion", "ONION", "onions", "ONION (RED)", "red onion", "kanda",
         "ಈರುಳ್ಳಿ", "कांदा"],
    )
    def test_onion_aliases_all_resolve(self, text):
        assert canonical_commodity(text) == "Onion"

    def test_kannada_and_marathi_reach_the_same_canonical(self):
        assert canonical_commodity("ಈರುಳ್ಳಿ") == canonical_commodity("कांदा")

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("ragi", "Ragi"), ("finger millet", "Ragi"),
            ("toor dal", "Tur"), ("arhar", "Tur"),
            ("chana", "Bengal Gram"), ("moong", "Green Gram"),
            ("urad", "Black Gram"), ("kapas", "Cotton"),
        ],
    )
    def test_common_indian_names(self, text, expected):
        assert canonical_commodity(text) == expected

    def test_similar_pulses_are_kept_distinct(self):
        # "green gram" must not collapse into "gram"/Bengal Gram. Conflating
        # these would merge two unrelated price series.
        assert canonical_commodity("green gram") == "Green Gram"
        assert canonical_commodity("bengal gram") == "Bengal Gram"
        assert canonical_commodity("black gram") == "Black Gram"

    def test_embedded_in_a_sentence(self):
        assert canonical_commodity("what is the price of red onion today") == "Onion"

    @pytest.mark.parametrize("text", ["", None, "xyzzy", "a totally unknown crop"])
    def test_unknown_returns_none_rather_than_guessing(self, text):
        assert canonical_commodity(text) is None

    def test_exactness_is_reported(self):
        assert resolve_commodity("Onion").exact is True
        assert resolve_commodity("price of onion please").exact is False


class TestMarketNames:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Gulbarga", "Kalaburagi"),          # renamed district
            ("Gulbarga APMC", "Kalaburagi"),
            ("KALABURGI", "Kalaburagi"),
            ("Lasalgaon Market Yard", "Lasalgaon"),
            ("Nashik Mandi", "Nashik"),
            ("Bangalore", "Bengaluru"),
            ("Bombay", "Mumbai"),
        ],
    )
    def test_canonicalisation(self, text, expected):
        assert normalize_market_name(text) == expected

    def test_unknown_market_is_preserved_not_dropped(self):
        assert normalize_market_name("Chikkanayakanahalli") == "Chikkanayakanahalli"

    def test_decoration_is_stripped_even_from_unknown_markets(self):
        # Consistent with Lasalgaon above: "Yard"/"Mandi" are decorations, so
        # the same yard spelled either way lands on one canonical name.
        assert normalize_market_name("Some New Yard") == "Some New"

    def test_empty_is_none(self):
        assert normalize_market_name("") is None
        assert normalize_market_name(None) is None


class TestUnits:
    @pytest.mark.parametrize(
        "text,expected",
        [("kg", "kg"), ("Kgs", "kg"), ("quintal", "quintal"), ("qtl", "quintal"),
         ("ton", "tonne"), ("MT", "tonne"), ("bags", "bag")],
    )
    def test_unit_aliases(self, text, expected):
        assert normalize_unit(text) == expected

    def test_unknown_unit_is_none(self):
        assert normalize_unit("furlong") is None

    def test_conversions(self):
        assert to_kg(1, "quintal") == 100
        assert to_kg(1, "tonne") == 1000
        assert to_kg(2, "bag") == 2 * ASSUMED_KG_PER_BAG

    def test_price_unit_conversions_round_trip(self):
        assert price_per_quintal_to_per_kg(2500) == 25
        assert price_per_kg_to_per_quintal(25) == 2500


class TestQuantityParsing:
    @pytest.mark.parametrize(
        "text,kg",
        [("50 kg", 50), ("2 quintal", 200), ("1 ton", 1000), ("half ton", 500),
         ("10 bags", 500), ("2.5 quintal", 250)],
    )
    def test_spoken_quantities(self, text, kg):
        parsed = parse_quantity(text)
        assert parsed is not None
        assert parsed.kg == pytest.approx(kg)

    def test_bag_weight_is_flagged_as_an_assumption(self):
        # A "bag" is not a unit of mass; the caller must know we assumed one.
        assert parse_quantity("10 bags").assumed is True
        assert parse_quantity("10 kg").assumed is False

    @pytest.mark.parametrize("text", ["", None, "some onions", "kg", "zero kg"])
    def test_unparseable_returns_none(self, text):
        assert parse_quantity(text) is None


class TestGrades:
    @pytest.mark.parametrize(
        "text,expected",
        [("A", "A"), ("grade a", "A"), ("FAQ", "FAQ"),
         ("fair average quality", "FAQ"), ("large", "A"), ("small", "C")],
    )
    def test_grade_aliases(self, text, expected):
        assert normalize_grade(text) == expected

    def test_unknown_grade_is_uppercased_not_dropped(self):
        assert normalize_grade("super special") == "SUPER_SPECIAL"
