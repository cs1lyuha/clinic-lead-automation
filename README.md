# 🦷 clinic-lead-automation

Automatizare de business pentru o **clinică stomatologică** din Moldova. Nu e un chatbot: e fluxul complet de la
„cineva a completat formularul pe Facebook” până la „managerul vede vineri câți pacienți au venit din fiecare reclamă”.

```
formular site / Facebook Lead Ads / Instagram ──(n8n sau direct)──▶ POST /webhooks/lead  (HMAC + anti-replay)
   → normalizare (telefoane MD → +373…, nume, e-mail)
   → deduplicare (același pacient în 30 de zile)
   → calificare AI (ro + ru): intenție, urgență, scor, valoare estimată, răspuns sugerat
   → CRM (HubSpot): contact + deal în etapa potrivită
   → Telegram către recepție: 🔥 urgențele imediat, cu răspunsul gata scris
   → raport săptămânal: surse, pâlnie, timp de răspuns, bani programați
```

> Pasul 5 din planul meu de studiu: automatizare pentru o nișă. Pagina de ofertă pentru client e în [`OFERTA.md`](OFERTA.md).

![CI](https://github.com/cs1lyuha/clinic-lead-automation/actions/workflows/ci.yml/badge.svg)

## Problema reală

Clinicile mici primesc cereri din 4–5 locuri (Facebook, Instagram, site, Google Ads, telefon), în **română și rusă**.
Recepția le citește când apucă, le trece de mână într-un tabel și sună în ordinea în care le-a văzut.
Un pacient cu durere care a scris la 9:05 poate aștepta după trei întrebări de preț, iar managerul nu știe care reclamă aduce pacienți și care aduce doar întrebări.

## Ce face, concret

| Pas | Detalii | Cod |
|---|---|---|
| **Webhook securizat** | HMAC-SHA256 peste `timestamp.body`, toleranță de 5 minute (anti-replay), comparație în timp constant. Semnătura se verifică pe bytes-ii exacți, **înainte** de parsare | `security.py` |
| **Idempotență** | `delivery_id` unic: retry-urile de la n8n / Facebook nu creează lead-uri dublate | `store.delivery_seen` |
| **Normalizare** | „069 123 456”, „+373 69123456”, „0037369123456” → `+37369123456`. Numerele invalide nu blochează lead-ul | `normalize.py` |
| **Deduplicare** | același telefon sau e-mail în 30 de zile = același pacient; se adaugă doar un eveniment `repeat_contact`. Excepție: **dacă revine cu durere, alerta pleacă oricum** | `pipeline.py` |
| **Calificare** | reguli ro/ru (offline) sau Claude Haiku 5.5 cu JSON strict. Dacă AI-ul pică sau răspunde invalid → **fallback pe reguli**, lead-ul nu se pierde | `qualify.py` |
| **Scor și prioritate** | calculate **în cod** din intenție, urgență, date de contact și cererea unei ore anume: 🔥 hot / 🟡 warm / 🔵 cold | `qualify.score` |
| **Răspuns sugerat** | în limba pacientului; recepția doar îl copiază | `qualify.REPLIES` |
| **CRM** | HubSpot API v3: caută contactul după e-mail sau telefon, îl actualizează sau îl creează, apoi face deal-ul în etapa potrivită. Dacă CRM-ul pică, lead-ul rămâne salvat, iar `retry_crm()` îl reia | `integrations.py` |
| **Notificări** | card Telegram: ⚠️ URGENȚĂ, contact, sursă/campanie, mesaj, răspuns sugerat. Spam-ul nu deranjează pe nimeni | `integrations.lead_card` |
| **Raport** | pâlnie new → contacted → booked → attended, conversie pe surse, timp median de răspuns (separat pentru urgențe), lei programați | `report.py` |

## Cifre (simulare)

`python -m leadflow.simulate`: o săptămână de lead-uri **generate** (seed fix), trimise prin webhook-ul semnat, ca în producție. Ipotezele sunt în `simulate.py`: 6–12 lead-uri pe zi, 8% spam, iar recepția sună în ordinea priorității.

| | |
|---|---|
| lead-uri reale / spam blocat | 53 / 3 |
| timp median până la primul contact | 27 min, **8 min la urgențe** |
| rată de programare | 55% (29 programați, 21 prezentați) |
| valoare programată | 121 900 lei |
| timp de triere economisit | ~2.8 ore pe săptămână (3 min/lead × 56 de mesaje) |
| limbă | 40 ro / 13 ru, cu răspuns sugerat în limba pacientului |

Cifrele sunt **ilustrative**, nu de la o clinică reală. Rostul simulării e să arate că raportul și fluxul merg cap-coadă și ce întrebări poate pune managerul. Pasul următor e un pilot cu date reale (vezi `OFERTA.md`).

## Pornire

```bash
git clone https://github.com/cs1lyuha/clinic-lead-automation.git
cd clinic-lead-automation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt uvicorn

pytest -q                              # 57 de teste
python -m leadflow.simulate            # raportul de mai sus

export LEADFLOW_WEBHOOK_SECRET=whsec_... LEADFLOW_API_KEY=...
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...       # opțional
export HUBSPOT_TOKEN=...                                  # opțional (private app)
export ANTHROPIC_API_KEY=...                              # opțional; altfel reguli
uvicorn "leadflow.api:create_app" --factory --port 8000
```

Trimiterea unui lead de test, cu semnătură:

```bash
BODY='{"delivery_id":"test-000001","name":"Ana Popescu","phone":"069123456","message":"Mă doare măseaua","source":"website"}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$LEADFLOW_WEBHOOK_SECRET" | cut -d' ' -f2)
curl -X POST localhost:8000/webhooks/lead -H "Content-Type: application/json" \
     -H "X-Leadflow-Timestamp: $TS" -H "X-Leadflow-Signature: sha256=$SIG" -d "$BODY"
```

## n8n sau Python?

Am ales **n8n doar pentru conectori** și **Python pentru logică**:

| Ce | Unde | De ce |
|---|---|---|
| Facebook Lead Ads, Instagram, Google Forms → webhook | n8n (noduri gata făcute, OAuth gestionat de n8n) | conectorii se schimbă des; în n8n îi actualizezi fără deploy |
| semnătură, deduplicare, scor, fallback AI, raport | Python | are nevoie de teste, versiuni, review și tratarea erorilor. În n8n, logica cu multe ramuri devine greu de testat și de urmărit |

În n8n fluxul e: *Facebook Lead Ads Trigger → Code (calculează HMAC cu `crypto`) → HTTP Request (POST /webhooks/lead)*.

## Structura

| Fișier | Rol |
|---|---|
| `leadflow/security.py` | semnare și verificare HMAC cu anti-replay |
| `leadflow/normalize.py` | payload-ul webhook-ului, telefoane, nume |
| `leadflow/qualify.py` | `RuleQualifier`, `ClaudeQualifier` (cu fallback), scor, răspunsuri ro/ru |
| `leadflow/store.py` | SQLite: lead-uri, livrări, evenimente, statusuri |
| `leadflow/integrations.py` | CRM (memorie / HubSpot), Telegram, cardul de notificare |
| `leadflow/pipeline.py` | fluxul complet, `retry_crm()` |
| `leadflow/api.py` | FastAPI: webhook, recepție, raport |
| `leadflow/report.py` | raport JSON și Markdown |
| `leadflow/simulate.py` | simularea unei săptămâni |

## Ce urmează

- [ ] Pilot cu o clinică reală (2 săptămâni, doar notificări + raport, fără CRM)
- [ ] Mesaj automat pe WhatsApp/Viber cu răspunsul sugerat, după aprobarea recepției
- [ ] Integrare cu agenda clinicii, pentru programare directă din chat
- [ ] Video demo de 2 minute
