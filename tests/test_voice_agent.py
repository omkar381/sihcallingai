"""
The voice agent in Kannada, Hindi and English: understanding, dialogue,
phrasing and call delivery.

These tests run without network. Gemini is replaced by the keyword path or by
constructing the understanding directly, and speech synthesis is stubbed, so
what is under test is the behaviour a farmer experiences on a call.
"""

from __future__ import annotations

import re
import time

import pytest

from app.market.normalization import COMMODITY_MASTER
from app.market.schema import SEED_MARKETS
from app.voice import language as lang
from app.voice.kannada import COMMODITY_KN, MARKET_KN
from app.voice.nlu import Understanding

FARMER = "+918618075133"
KANNADA = re.compile(r"[ಀ-೿]")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_settings(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "MARKET_DEMO_MODE", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")   # never reach the network
    return settings


@pytest.fixture
def world(trade_db, demo_settings):
    """Demo prices for onion and tur, and the demo marketplace around FARMER."""
    from app.market.demo_data import seed_demo_prices
    from app.trade.demo_seed import seed_trade_demo

    seed_demo_prices(commodities=["Onion", "Tur"], days=100)
    seed_trade_demo(FARMER)
    return FARMER


def say(state, intent, **slots):
    from app.voice import dialogue

    return dialogue.respond(state, Understanding(intent=intent, english=slots.pop("english", ""), **slots))


def new_state(sid="CAtest", language="kn"):
    from app.voice import dialogue

    dialogue.end_state(sid)
    state = dialogue.get_state(sid, FARMER)
    state.language = language
    return state


# ---------------------------------------------------------------------------
# Languages and phrasing
# ---------------------------------------------------------------------------

class TestLanguages:
    def test_language_names_in_any_script_resolve(self):
        for name, code in (("Kannada", "kn"), ("ಕನ್ನಡ", "kn"), ("हिंदी", "hi"), ("hindi", "hi"),
                           ("English", "en"), ("अंग्रेज़ी", "en"), ("en", "en")):
            assert lang.normalise_language(name) == code
        assert lang.normalise_language("tamil", default=None) is None

    def test_menu_accepts_keys_and_spoken_names(self):
        assert lang.language_from_choice("1", "") == "kn"
        assert lang.language_from_choice("2", "") == "hi"
        assert lang.language_from_choice("", "English please") == "en"
        assert lang.language_from_choice("", "हिंदी") == "hi"
        assert lang.language_from_choice("", "") is None

    def test_prices_are_plain_integers_in_indian_languages(self):
        assert lang.speaker("kn").rupees(2760.4) == "2760 ರೂಪಾಯಿ"
        assert lang.speaker("hi").rupees(2760.4) == "2760 रुपये"
        assert lang.speaker("en").rupees(2760.4) == "₹2,760"

    def test_speech_text_never_contains_symbols(self):
        spoken = lang.clean_for_speech("₹10,588 (approx) up 16% **today**", "kn")
        for symbol in ("₹", ",", "(", "%", "*"):
            assert symbol not in spoken
        assert "10588" in spoken

    def test_english_speech_reads_rupees_and_distances_as_words(self):
        spoken = lang.clean_for_speech("Solapur is 136 km away and leaves ₹1,23,938.", "en")
        assert "123938 rupees" in spoken
        assert "136 kilometres" in spoken

    def test_totals_are_rounded_the_way_people_say_them(self):
        assert lang.approx(10588) == 10600
        assert lang.approx(123938) == 124000
        assert lang.approx(842) == 840

    def test_quantities_use_quintal_above_a_hundred_kilos(self):
        assert lang.speaker("kn").qty(3000) == "30 ಕ್ವಿಂಟಲ್"
        assert lang.speaker("hi").qty(250) == "2.5 क्विंटल"
        assert lang.speaker("en").qty(40) == "40 kg"

    @pytest.mark.parametrize("names", [COMMODITY_KN, lang.COMMODITY_HI])
    def test_every_commodity_is_named(self, names):
        assert [c.canonical for c in COMMODITY_MASTER if c.canonical not in names] == []

    @pytest.mark.parametrize("names", [MARKET_KN, lang.MARKET_HI])
    def test_every_seeded_market_is_named(self, names):
        assert [m[0] for m in SEED_MARKETS if m[0] not in names] == []

    def test_personal_greetings(self):
        assert lang.greeting_text("kn", "Rajesh").startswith("ನಮಸ್ಕಾರ Rajesh!")
        assert lang.greeting_text("hi", "Rajesh").startswith("नमस्ते Rajesh!")
        assert lang.greeting_text("en", "Rajesh").startswith("Hello Rajesh!")

    def test_every_prompt_exists_in_every_language(self):
        keys = set(lang.SYSTEM_PROMPTS["kn"])
        for code in lang.SUPPORTED:
            assert set(lang.SYSTEM_PROMPTS[code]) == keys


# ---------------------------------------------------------------------------
# Understanding without Gemini
# ---------------------------------------------------------------------------

class TestKeywordUnderstanding:
    @pytest.fixture(autouse=True)
    def offline(self, demo_settings, monkeypatch):
        self.gloss = {}
        self.translated = []

        def fake_translate(text, source="kn"):
            self.translated.append(source)
            return self.gloss.get(text, "")

        monkeypatch.setattr("app.translation.translate_to_english", fake_translate)

    def understand(self, transcript, english="", language="kn"):
        from app.voice.nlu import understand

        self.gloss[transcript] = english
        return understand(transcript, language=language)

    def test_sell_now_or_wait_is_advice_not_a_listing(self):
        # The old router matched "sell" and created an auction listing.
        u = self.understand("ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ", "Should I sell now or wait?")
        assert u.intent == "SELL_OR_WAIT"
        assert u.source == "keywords"

    def test_who_will_buy_finds_buyers_not_an_input_order(self):
        # The old router matched "buy" and proposed a fertiliser order.
        u = self.understand("ನನ್ನ ಈರುಳ್ಳಿ ಯಾರು ಖರೀದಿ ಮಾಡ್ತಾರೆ", "Who will buy my onion?")
        assert u.intent == "FIND_BUYERS"
        assert u.commodity == "Onion"

    def test_crop_and_market_are_read_from_kannada_directly(self):
        u = self.understand("ಕಲ್ಬುರ್ಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು")
        assert (u.intent, u.commodity, u.location) == ("PRICE", "Onion", "Kalaburagi")

    def test_hindi_is_understood_and_translated_from_hindi(self):
        u = self.understand("कलबुर्गी में प्याज का भाव क्या है", language="hi")
        assert (u.intent, u.commodity, u.location) == ("PRICE", "Onion", "Kalaburagi")
        assert self.translated == ["hi"]

    def test_hindi_sell_or_wait(self):
        assert self.understand("अभी बेचूँ या रुकूँ", language="hi").intent == "SELL_OR_WAIT"

    def test_english_is_not_sent_for_translation(self):
        u = self.understand("Who will buy my onion?", language="en")
        assert u.intent == "FIND_BUYERS"
        assert self.translated == []

    def test_asking_for_another_language(self):
        for text, code in (("हिंदी में बोलो", "hi"), ("English please", "en"), ("ಕನ್ನಡದಲ್ಲಿ ಮಾತಾಡಿ", "kn")):
            u = self.understand(text)
            assert (u.intent, u.language_choice) == ("CHANGE_LANGUAGE", code), text

    def test_spoken_numbers_become_a_quantity(self):
        u = self.understand("I have thirty quintals of onion, which market is best?", language="en")
        assert u.intent == "COMPARE_MARKETS"
        assert u.quantity_kg == 3000

    def test_storage_and_urgency_are_extracted(self):
        u = self.understand("I can store it for 20 days, I need money urgently", language="en")
        assert u.storage_days == 20
        assert u.needs_cash_urgently is True

    def test_an_unknown_crop_is_kept_as_said_not_guessed(self):
        from app.voice.nlu import _finalise

        u = _finalise(Understanding(intent="PRICE"), "", "Dragon Fruit")
        assert u.commodity is None
        assert u.commodity_raw == "Dragon Fruit"


# ---------------------------------------------------------------------------
# Dialogue
# ---------------------------------------------------------------------------

class TestDialogue:
    def test_unsupported_crop_never_answers_with_the_previous_crop(self, world):
        state = new_state()
        say(state, "PRICE", commodity="Onion", location="Kalaburagi")
        reply = say(state, "PRICE", commodity_raw="Dragon Fruit")
        assert "Dragon Fruit" in reply.en
        assert "₹" not in reply.en

    def test_demo_caveat_is_said_once_and_before_the_question(self, world):
        state = new_state()
        first = say(state, "PRICE", commodity="Onion", location="Kalaburagi")
        assert lang.DEMO_CAVEAT["en"] in first.en
        assert lang.DEMO_CAVEAT["kn"] in first.text
        assert first.en.rstrip().endswith("?")
        assert first.en.index(lang.DEMO_CAVEAT["en"]) < first.en.rindex("Shall I")
        second = say(state, "PRICE", commodity="Tur")
        assert lang.DEMO_CAVEAT["en"] not in second.en

    def test_market_comparison_asks_for_quantity_first(self, world):
        state = new_state()
        ask = say(state, "COMPARE_MARKETS", commodity="Onion", location="Kalaburagi")
        assert "How much" in ask.en
        answer = say(state, "PROVIDE_DETAILS", quantity_kg=3000)
        assert answer.intent == "COMPARE_MARKETS"
        assert "per quintal" in answer.en

    def test_suggested_next_step_runs_on_yes(self, world):
        state = new_state()
        say(state, "PRICE", commodity="Onion", location="Kalaburagi")
        assert say(state, "YES").intent == "COMPARE_MARKETS"

    def test_sell_or_wait_asks_for_constraints_before_advising(self, world):
        state = new_state()
        ask = say(state, "SELL_OR_WAIT", commodity="Onion", location="Kalaburagi")
        assert "store" in ask.en and "urgently" in ask.en
        advice = say(state, "PROVIDE_DETAILS", storage_days=0, needs_cash_urgently=True)
        assert advice.intent == "SELL_OR_WAIT"
        assert "wait about" not in advice.en.lower()

    def test_accepting_an_offer_needs_an_explicit_yes(self, world):
        from app.trade import offers, payments

        state = new_state()
        read_out = say(state, "MY_OFFERS")
        assert "accept" in read_out.en.lower()
        say(state, "NO")
        assert payments.list_payments(payee_phone=FARMER, status="PENDING") == []

        say(state, "MY_OFFERS")
        done = say(state, "YES")
        assert done.intent == "ACCEPT_OFFER"
        assert payments.list_payments(payee_phone=FARMER, status="PENDING")
        assert offers.list_offers(status="ACCEPTED")

    def test_payment_status_answers_about_the_crop_named(self, world):
        reply = say(new_state(), "PAYMENT_STATUS", commodity="Tur")
        assert "Tur" in reply.en
        assert "escrow" in reply.en

    def test_listing_by_voice_creates_a_published_lot(self, world):
        from app.trade import lots

        state = new_state()
        before = len(lots.list_lots(farmer_phone=FARMER))
        say(state, "LIST_FOR_SALE", commodity="Onion", quantity_kg=500)
        confirm = say(state, "PROVIDE_DETAILS", grade="B")
        assert "Say yes or no" in confirm.en
        say(state, "YES")
        mine = lots.list_lots(farmer_phone=FARMER)
        assert len(mine) == before + 1
        assert any(l["quantity_kg"] == 500 and l["status"] == "PUBLISHED" for l in mine)

    def test_complaint_on_unpaid_deal_does_not_promise_escrow(self, world):
        state = new_state()
        say(state, "MY_OFFERS")
        say(state, "YES")                              # creates a PENDING onion payment
        ask = say(state, "RAISE_COMPLAINT", complaint_category="PAYMENT", commodity="Onion")
        assert "Say yes or no" in ask.en
        filed = say(state, "YES")
        assert "Do not dispatch" in filed.en

    def test_fpo_pooling_describes_the_farmers_own_pool(self, world):
        reply = say(new_state(), "FPO_POOLING")
        assert "including you" in reply.en
        assert "  " not in reply.text

    def test_goodbye_ends_the_call(self, world):
        assert say(new_state(), "GOODBYE").hangup is True


class TestLanguagesInConversation:
    def run_everything(self, language):
        """One of every answer, collected, in `language`."""
        state = new_state(language=language)
        replies = [
            say(state, "PRICE", commodity="Onion", location="Kalaburagi"),
            say(state, "PRICE_TREND", commodity="Onion"),
            say(state, "COMPARE_MARKETS", commodity="Onion", quantity_kg=3000),
            say(state, "SELL_OR_WAIT", commodity="Onion", storage_days=20, needs_cash_urgently=False),
            say(state, "FIND_BUYERS", commodity="Onion"),
            say(state, "MY_OFFERS"),
            say(state, "NO"),
            say(state, "PAYMENT_STATUS", commodity="Tur"),
            say(state, "FPO_POOLING"),
            say(state, "PRICE", commodity_raw="Dragon Fruit"),
            say(state, "HELP"),
        ]
        return replies

    def test_hindi_replies_are_all_hindi(self, world):
        for reply in self.run_everything("hi"):
            assert reply.language == "hi"
            assert DEVANAGARI.search(reply.text), reply.intent
            assert not KANNADA.search(reply.text), (reply.intent, reply.text)

    def test_kannada_replies_contain_no_hindi(self, world):
        for reply in self.run_everything("kn"):
            assert KANNADA.search(reply.text), reply.intent
            assert not DEVANAGARI.search(reply.text), (reply.intent, reply.text)

    def test_english_replies_are_the_english_text(self, world):
        for reply in self.run_everything("en"):
            assert reply.text == reply.en
            assert not KANNADA.search(reply.text) and not DEVANAGARI.search(reply.text)

    def test_same_figures_in_every_language(self, world):
        figures = {}
        for code in lang.SUPPORTED:
            reply = say(new_state(language=code), "PRICE", commodity="Onion", location="Kalaburagi")
            figures[code] = sorted(re.findall(r"\d+", reply.text.replace(",", "")))
        assert figures["kn"] == figures["hi"] == figures["en"]

    def test_hindi_caveat_comes_before_the_question(self, world):
        reply = say(new_state(language="hi"), "PRICE", commodity="Onion", location="Kalaburagi")
        assert lang.DEMO_CAVEAT["hi"] in reply.text
        assert reply.text.rstrip().endswith("?")

    def test_switching_language_mid_call(self, world):
        state = new_state(language="kn")
        switched = say(state, "CHANGE_LANGUAGE", language_choice="hi")
        assert state.language == "hi"
        assert DEVANAGARI.search(switched.text)
        after = say(state, "PRICE", commodity="Onion", location="Kalaburagi")
        assert after.language == "hi" and DEVANAGARI.search(after.text)

    def test_unclear_language_request_asks_which(self, world):
        state = new_state(language="kn")
        reply = say(state, "CHANGE_LANGUAGE")
        assert state.language == "kn"
        assert "Kannada, Hindi or English" in reply.en


# ---------------------------------------------------------------------------
# Call delivery
# ---------------------------------------------------------------------------

@pytest.fixture
def twilio_client(trade_db, demo_settings, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.twilio_handlers as handlers

    monkeypatch.setattr(demo_settings, "VALIDATE_TWILIO_SIGNATURE", False)
    monkeypatch.setattr(demo_settings, "BASE_URL", "https://example.test")
    monkeypatch.setattr(
        handlers.speech, "system_prompt_file", lambda key, language="kn": f"sys_{language}_{key}.mp3"
    )
    app = FastAPI()
    app.include_router(handlers.router)
    return TestClient(app), handlers


CALL = {"CallSid": "CA" + "0" * 32, "From": "+17372212163", "To": FARMER}


def test_first_call_asks_for_a_language_in_all_three(twilio_client):
    client, _ = twilio_client
    xml = client.post("/twilio/voice", data=CALL).text
    assert 'input="dtmf speech"' in xml
    assert "/twilio/language" in xml
    for code in ("kn", "hi", "en"):
        assert f"sys_{code}_menu.mp3" in xml


def test_choosing_hindi_greets_and_listens_in_hindi_and_is_remembered(twilio_client):
    from app.voice import call_log

    client, _ = twilio_client
    client.post("/twilio/voice", data=CALL)
    xml = client.post("/twilio/language", data={**CALL, "Digits": "2"}).text
    assert 'language="hi-IN"' in xml
    assert "sys_hi_greeting.mp3" in xml
    assert call_log.get_language_preference(FARMER) == "hi"

    again = client.post("/twilio/voice", data={**CALL, "CallSid": "CA" + "2" * 32}).text
    assert 'input="dtmf speech"' not in again
    assert 'language="hi-IN"' in again


def test_unanswered_menu_repeats_once_then_uses_kannada(twilio_client):
    client, _ = twilio_client
    sid = {**CALL, "CallSid": "CA" + "3" * 32}
    first = client.post("/twilio/language", data=sid).text
    assert "sys_kn_menu.mp3" in first
    second = client.post("/twilio/language", data=sid).text
    assert 'language="kn-IN"' in second and "sys_kn_greeting.mp3" in second


def test_outbound_language_skips_the_menu(twilio_client):
    client, _ = twilio_client
    xml = client.post("/twilio/voice?lang=en", data={**CALL, "To": "+919999999999"}).text
    assert 'language="en-IN"' in xml
    assert "sys_en_greeting.mp3" in xml


def test_listening_uses_hints_for_the_language(twilio_client):
    client, _ = twilio_client
    xml = client.post("/twilio/voice?lang=kn", data=CALL).text
    assert 'actionOnEmptyResult="true"' in xml
    assert "ಈರುಳ್ಳಿ" in xml


def test_gather_holds_the_line_without_waiting_for_the_answer(twilio_client, monkeypatch):
    client, handlers = twilio_client

    def slow_turn(*args, **kwargs):
        time.sleep(2)
        raise RuntimeError("never delivered in this test")

    monkeypatch.setattr(handlers, "run_turn", slow_turn)
    started = time.time()
    xml = client.post("/twilio/gather", data={**CALL, "SpeechResult": "ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು"}).text
    assert time.time() - started < 1.0
    assert "sys_kn_hold.mp3" in xml
    assert "/twilio/answer?attempt=1" in xml


def test_ready_answer_is_played_interruptibly_in_its_language(twilio_client):
    client, handlers = twilio_client
    handlers._begin_answer(CALL["CallSid"])
    handlers._update_answer(
        CALL["CallSid"], status="ready", audio_url="https://example.test/audio/r.mp3",
        hangup=False, language="hi",
    )
    xml = client.post("/twilio/answer?attempt=2", data=CALL).text
    gather = xml[xml.index("<Gather"):xml.index("</Gather>")]
    assert "<Play>https://example.test/audio/r.mp3</Play>" in gather
    assert 'language="hi-IN"' in gather


def test_answer_still_working_polls_again(twilio_client):
    client, handlers = twilio_client
    handlers._begin_answer(CALL["CallSid"])
    xml = client.post("/twilio/answer?attempt=2", data=CALL).text
    assert "/twilio/answer?attempt=3" in xml


def test_repeated_silence_ends_politely(twilio_client):
    client, _ = twilio_client
    sid = {**CALL, "CallSid": "CA" + "1" * 32, "SpeechResult": ""}
    first = client.post("/twilio/gather", data=sid).text
    assert "sys_kn_reprompt.mp3" in first and "<Hangup" not in first
    client.post("/twilio/gather", data=sid)
    third = client.post("/twilio/gather", data=sid).text
    assert "sys_kn_goodbye.mp3" in third and "<Hangup" in third
