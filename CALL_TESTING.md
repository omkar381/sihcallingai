# Testing the Kannada Voice Agent

Every expected reply below is copied from a verified run on **2026-09-14**, with the
demo marketplace seeded around `+91 86180 75133`.

---

## 1. Start everything

```bash
python start_calling.py
```

Starts ngrok, writes the new public URL into `.env`, starts the server, prepares the
Kannada voice prompts, and confirms Twilio can reach the webhook. Wait for `READY`.

```bash
python start_calling.py --status
```

```bash
python start_calling.py --stop
```

## 2. Seed the demo marketplace (once)

Creates verified buyers, an FPO with neighbours, two open offers on your onion, and a
tur sale with money in escrow, all around your number, so every flow has something
to talk about. Every entity is registered as a demo fixture.

Open **http://localhost:8001/farmer**, enter your number, click **Seed demo**
(it asks for the operator key: `API_KEY` in `.env`). Already done for `+91 86180 75133`.

## 3. Try it without a phone

Open **http://localhost:8001/calls**. Type or speak Kannada (microphone button,
Chrome), or click an example. It runs the exact phone pipeline and plays the audio.

## 4. Place the call

```bash
python call.py --check
```

```bash
python call.py +918618075133
```

Or press **Call me** on the Farmer page. The transcript appears live on `/calls`.

---

## What to say, and what comes back

After you speak you hear **"ಒಂದು ಕ್ಷಣ, ನೋಡುತ್ತಿದ್ದೇನೆ"**, then the answer in 4 to 7
seconds. You can **talk over the answer** to interrupt it. Every answer ends by
offering the next step, so **"ಹೌದು"** (yes) moves the conversation along.

### 1. Price

> **ಕಲಬುರಗಿಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಎಷ್ಟು?**

> ಕಲಬುರಗಿ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಈರುಳ್ಳಿ ಬೆಲೆ ಕ್ವಿಂಟಲ್‌ಗೆ 2760 ರೂಪಾಯಿ ಇದೆ, ಅಂದರೆ ಕೆಜಿಗೆ ಸುಮಾರು 28 ರೂಪಾಯಿ. ಕನಿಷ್ಠ 2480, ಗರಿಷ್ಠ 3040 ರೂಪಾಯಿ ತನಕ ವ್ಯಾಪಾರ ಆಗಿದೆ. ಒಂದು ಮುಖ್ಯ ವಿಷಯ: ಈಗ ಹೇಳುತ್ತಿರುವ ಬೆಲೆ ಮತ್ತು ವ್ಯಾಪಾರಿಗಳ ಮಾಹಿತಿ ಪ್ರಾಯೋಗಿಕ ಮಾಹಿತಿ, ನಿಜವಾದ ಮಾರುಕಟ್ಟೆ ದರ ಅಲ್ಲ. ಖರ್ಚು ಕಳೆದು ಬೇರೆ ಹತ್ತಿರದ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಹೆಚ್ಚು ಸಿಗುತ್ತಾ ಅಂತ ನೋಡಬೇಕಾ?
>
> *₹2,760 per quintal, about ₹28 a kilo, traded ₹2,480 to ₹3,040. Demo-data note (said once per call). Shall I check whether a nearby market pays more after costs?*

### 2. Best market: say **ಹೌದು**, then the quantity

> **ಹೌದು** → *"How much onion do you have?"* → **ಮೂವತ್ತು ಕ್ವಿಂಟಲ್**

> ನಿಮ್ಮ 30 ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಕಲಬುರಗಿ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಮಾರಿದರೆ, ಸಾಗಣೆ ಮತ್ತು ಕಮಿಷನ್ ಕಳೆದು ಕ್ವಿಂಟಲ್‌ಗೆ ಸುಮಾರು 2532 ರೂಪಾಯಿ ಕೈಗೆ ಬರುತ್ತದೆ. ಸೊಲ್ಲಾಪುರ ಮಾರುಕಟ್ಟೆ 136 ಕಿಲೋಮೀಟರ್ ದೂರ ಇದೆ, ಆದರೆ ಅಲ್ಲಿ ಎಲ್ಲಾ ಖರ್ಚು ಕಳೆದು ಕ್ವಿಂಟಲ್‌ಗೆ 2884 ರೂಪಾಯಿ ಸಿಗುತ್ತದೆ. ಅಂದರೆ ನಿಮ್ಮ ಪೂರ್ತಿ ಸರಕಿಗೆ ಸುಮಾರು 10600 ರೂಪಾಯಿ ಹೆಚ್ಚು. ಈ ಬೆಳೆ ಖರೀದಿಸುವ ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳನ್ನು ಹುಡುಕಬೇಕಾ?
>
> *At Kalaburagi you keep about ₹2,532 per quintal after transport and commission. Solapur is 136 km away but leaves ₹2,884. About ₹10,600 more for the whole lot.*

**The demo moment:** the higher headline price is only worth it once freight is paid,
and the agent ranks on what the farmer keeps.

### 3. Trend

> **ಈರುಳ್ಳಿ ಬೆಲೆ ಏರುತ್ತಿದೆಯಾ?**

> ಕಲಬುರಗಿ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಈರುಳ್ಳಿ ಈಗ ಕ್ವಿಂಟಲ್‌ಗೆ 2760 ರೂಪಾಯಿ. ಕಳೆದ ಒಂದು ವಾರದಲ್ಲಿ ಬೆಲೆ 10 ಶೇಕಡಾ ಏರಿದೆ. ಕಳೆದ ಒಂದು ತಿಂಗಳ ಸರಾಸರಿ 2579 ರೂಪಾಯಿ. ಮಾರುಕಟ್ಟೆಗೆ ಬರುತ್ತಿರುವ ಸರಕಿನ ಪ್ರಮಾಣ 7 ಶೇಕಡಾ ಕಡಿಮೆಯಾಗಿದೆ. ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ ಅಂತ ಸಲಹೆ ಬೇಕಾ?
>
> *Up 10% this week, month average ₹2,579, arrivals down 7%.*

### 4. Sell or wait: it asks two things first

> **ಈಗ ಮಾರಬೇಕಾ ಕಾಯಬೇಕಾ?** → *"How many days can you store it, and do you need money urgently?"*
> → **ಇಪ್ಪತ್ತು ದಿನ ಇಡಬಹುದು, ಹಣದ ಅವಸರ ಇಲ್ಲ**

> ನನ್ನ ಸಲಹೆ: ಈಗಲೇ ಮಾರಿ, ಆದರೆ ನಿಮ್ಮ ಹತ್ತಿರದ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಅಲ್ಲ. ಕಾರಣ, ಈಗಿನ ಬೆಲೆ ಕಳೆದ ಮೂರು ತಿಂಗಳ ಸರಾಸರಿಗಿಂತ 17 ಶೇಕಡಾ ಹೆಚ್ಚಿದೆ, ಕಳೆದ ವಾರದಿಂದ ಬೆಲೆ ಏರುತ್ತಿದೆ. ಸೊಲ್ಲಾಪುರ ಮಾರುಕಟ್ಟೆಯಲ್ಲಿ ಸಾಗಣೆ ಖರ್ಚು ಕಳೆದೂ ಕ್ವಿಂಟಲ್‌ಗೆ ಸುಮಾರು 353 ರೂಪಾಯಿ ಹೆಚ್ಚು ಸಿಗುತ್ತದೆ. ಈ ಸಲಹೆಯ ಖಚಿತತೆ ಕಡಿಮೆ. ಖರೀದಿಸುವ ಪರಿಶೀಲಿತ ವ್ಯಾಪಾರಿಗಳನ್ನು ಹುಡುಕಬೇಕಾ?

**Try again saying you have no storage and need money now.** The advice changes, and it
will never tell a farmer without storage to wait.

### 5. Offers, which need an explicit yes

> **ನನಗೆ ಆಫರ್ ಬಂದಿದೆಯಾ?**

> ನಿಮ್ಮ ಸರಕಿಗೆ 2 ಆಫರ್ ಬಂದಿವೆ. ಅತಿ ಹೆಚ್ಚಿನದು Basava Agri Traders ಅವರದು: 30 ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ, ಕ್ವಿಂಟಲ್‌ಗೆ 2950 ರೂಪಾಯಿ, ಒಟ್ಟು ಸುಮಾರು 88500 ರೂಪಾಯಿ, 3 ದಿನದಲ್ಲಿ ಪಾವತಿ. ಇವರ ಹಣ ಪಾವತಿ ದಾಖಲೆ ಚೆನ್ನಾಗಿದೆ. ಇದು ಕಲಬುರಗಿ ಮಾರುಕಟ್ಟೆಯ ಬೆಲೆಗಿಂತ ಕ್ವಿಂಟಲ್‌ಗೆ 190 ರೂಪಾಯಿ ಹೆಚ್ಚು. ಈ ಆಫರ್ ಒಪ್ಪಿಕೊಳ್ಳಬೇಕಾ?
>
> *Best offer ₹2,950/qtl, strong payment record, ₹190 above the market price. Accept?*

- **ಬೇಡ** → *"ಸರಿ, ಆಗಲಿ. ಬೇರೆ ಏನು ಸಹಾಯ ಬೇಕು?"* Nothing changes.
- **ಹೌದು** → accepted, a payment is created, and it warns: *send the produce only after
  the buyer deposits the money in escrow.* The Farmer page updates at once.

> Accepting is a real change. To demo it again later, seed a fresh number.

### 6. Payment

> **ನನ್ನ ತೊಗರಿ ಹಣ ಬಂತಾ?**

> Deccan Dal Mills ಅವರೊಂದಿಗಿನ ತೊಗರಿ ವ್ಯವಹಾರ: ಸುಮಾರು 85800 ರೂಪಾಯಿ ಸುರಕ್ಷಿತ ಖಾತೆಯಲ್ಲಿ ಜಮಾ ಆಗಿದೆ. ಈಗ ಸರಕು ಕಳುಹಿಸಬಹುದು. ಸರಕು ತಲುಪಿದ ಮೇಲೆ ಹಣ ನಿಮ್ಮ ಖಾತೆಗೆ ಬರುತ್ತದೆ.
>
> *About ₹85,800 is in escrow; you can dispatch now; it is released on delivery.*

### 7. FPO pooling

> **ನಮ್ಮ ಎಫ್‌ಪಿಒ ಜೊತೆ ಒಟ್ಟಾಗಿ ಮಾರಿದರೆ ಲಾಭ ಆಗುತ್ತಾ?**

> Kalyana Raitha Producer Company ಸಂಸ್ಥೆಯಲ್ಲಿ ನೀವೂ ಸೇರಿ 5 ರೈತರು ಒಟ್ಟು 32 ಕ್ವಿಂಟಲ್ ಮಧ್ಯಮ ದರ್ಜೆ ಈರುಳ್ಳಿ ಮಾರಾಟಕ್ಕೆ ಇಟ್ಟಿದ್ದಾರೆ. ಎಲ್ಲರೂ ಒಂದೇ ವಾಹನದಲ್ಲಿ ಕಳುಹಿಸಿದರೆ, ಬೇರೆ ಬೇರೆಯಾಗಿ ಕಳುಹಿಸುವುದಕ್ಕಿಂತ ಒಟ್ಟು ಸುಮಾರು 8600 ರೂಪಾಯಿ ಹೆಚ್ಚು ಉಳಿಯುತ್ತದೆ, ಅಂದರೆ ಪ್ರತಿ ರೈತನಿಗೆ ಸುಮಾರು 1700 ರೂಪಾಯಿ. ಒಟ್ಟುಗೂಡಿಸುವ ನಿರ್ಧಾರವನ್ನು ನಿಮ್ಮ ಸಂಸ್ಥೆಯ ವ್ಯವಸ್ಥಾಪಕರು ಮಾಡುತ್ತಾರೆ.
>
> *Including you, 5 farmers have 32 qtl of grade B onion. One vehicle keeps about ₹8,600 more than shipping separately, about ₹1,700 per farmer.*

The FPO page shows the exact figure, **+₹8,559 (₹1,712 per farmer)**, priced at Solapur.
Voice and screen use one calculation, so they cannot disagree.

### 8. Complaint, which also needs a yes

> **ವ್ಯಾಪಾರಿ ತೂಕದಲ್ಲಿ ಮೋಸ ಮಾಡಿದ್ದಾನೆ, ದೂರು ಕೊಡಬೇಕು** → reads back the deal → **ಹೌದು**

It files the complaint. If money is in escrow it stays frozen; if the deal is unpaid it
tells you not to dispatch. It never claims money is held when it is not.

### 9. List produce, which asks quality and then confirms

> **ಇಪ್ಪತ್ತು ಕ್ವಿಂಟಲ್ ಈರುಳ್ಳಿ ಮಾರಾಟಕ್ಕೆ ಹಾಕಬೇಕು** → *"quality?"* → **ಉತ್ತಮ** → reads back → **ಹೌದು**

Listed against your number; it tells you how many verified buyers' demand it matches.

### 10. Honest refusal

> **ಡ್ರ್ಯಾಗನ್ ಫ್ರೂಟ್ ಬೆಲೆ ಎಷ್ಟು?**

> ಕ್ಷಮಿಸಿ, Dragon Fruit ಬೆಳೆಯ ಮಾರುಕಟ್ಟೆ ಮಾಹಿತಿ ನನ್ನ ಬಳಿ ಇಲ್ಲ. ಈರುಳ್ಳಿ, ತೊಗರಿ, ಹತ್ತಿ, ಮೆಕ್ಕೆಜೋಳ, ಟೊಮೆಟೊ ಇಂತಹ ಬೆಳೆಗಳ ಬಗ್ಗೆ ಕೇಳಬಹುದು. ಯಾವ ಬೆಳೆ?

It never answers with a different crop's price.

### Ending

> **ಧನ್ಯವಾದ** → goodbye, and it hangs up. Staying silent twice also ends the call politely.

---

## If something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| "An application error has occurred" | Webhook unreachable (usually ngrok restarted) | `python start_calling.py`, then `python call.py --check` |
| Silence after the greeting | Server died | `tail -n 50 output/server.err` |
| Answers in English words, or gets the intent wrong | Gemini unavailable; keyword fallback in use | The `/calls` transcript shows a `keywords fallback` tag; check `GEMINI_API_KEY` |
| `21219 not verified` | Trial accounts only call verified numbers | Verify the number in the Twilio console |
| Offers already accepted | You said yes earlier | Seed a fresh number from the Farmer page |

Watch it live:

```bash
tail -f output/server.err
```

## Known limits

- **Latency 4–7 s per turn.** Understanding takes about 2 s; Microsoft's free TTS about
  1 s per 110 characters. Parallel synthesis was measured slower and not kept. Repeated
  sentences are cached and instant. Google Cloud TTS (set `GOOGLE_APPLICATION_CREDENTIALS`)
  would cut the TTS share substantially.
- **All prices, buyers and offers are demonstration data**, and the agent says so once per call.
  Set `DATA_GOV_API_KEY` for live Agmarknet prices.
- **Twilio trial:** calls start with Twilio's trial notice; only verified numbers can be called.
- **Webhook signatures are not verified** until `TWILIO_AUTH_TOKEN` is set in `.env`.
