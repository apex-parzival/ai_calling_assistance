# AI Clinic Voice Receptionist

AI phone receptionist for a medical clinic in Calgary, Alberta. One shared codebase,
one `clinics/<name>/` folder per clinic. See [`CLAUDE.md`](CLAUDE.md) for the full build brief.

**Status:** Phase 1 code complete and tested against the real LLM in text mode (22/22 scenarios).
A real voice call still needs the LiveKit secret. See [Phase 1 status](#phase-1-status) and the
full [task list](docs/TASKS.md).

## Layout

```
core/                 shared by every clinic
  config.py           clinic.yaml schema + validation
  settings.py         secrets/env + key validation; check_env.py is the CLI
  agent.py            LiveKit agent (STT -> LLM -> TTS), guardrail hooks, silence/turn/time limits
  prompts.py          system prompt built from clinic.yaml
  tools.py            the five tools; validation, read-back check, fallbacks
  guardrails.py       emergency / crisis detection, clinical-question flag, advice filter
  backend_client.py   agent -> backend HTTP, with spool-to-disk if the backend is down
  providers/          stt / tts / llm adapters
backend/              FastAPI: agent API, staff API, auth, audit log, WebSocket, booking adapters
dashboard/            Next.js front desk
clinics/<name>/       clinic.yaml (committed) + .env (secrets, gitignored)
deploy/               Dockerfiles + docker-compose.yml
tests/                pytest suite + tests/scenarios/simulate_call.py
docs/phase1-testing.md  Playground scenario script and latency procedure
```

## Prerequisites

- Python 3.11+ (developed on 3.14; Docker images use 3.12)
- Node.js 20+
- Docker Desktop is optional: without it the backend uses a local SQLite file.

### Vendor accounts (you create these — they need your email, card and 2FA)

| Service | Get | Put in `.env` |
| --- | --- | --- |
| [LiveKit Cloud](https://cloud.livekit.io) | Project URL + API key/secret | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| [Deepgram](https://console.deepgram.com) | API key | `DEEPGRAM_API_KEY` |
| [Cartesia](https://play.cartesia.ai) | API key + pick a voice | `CARTESIA_API_KEY`; voice ID goes in `clinic.yaml` → `voice.voice_id` |
| [Groq](https://console.groq.com) or [OpenRouter](https://openrouter.ai) | API key, a Qwen 7B–14B model ID | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` |
| [Twilio](https://console.twilio.com) | Account SID + auth token (number comes in Phase 2) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` |
| SendGrid or Twilio SMS | Staff notifications (optional during dev) | `NOTIFY_CHANNEL` + its keys |

In the LiveKit project settings, leave session recording **off**. The agent already passes
`record=False`, so no call audio or transcripts are uploaded to LiveKit Cloud.

## Setup

```bash
# Secrets for the demo clinic
cp clinics/demo_clinic/.env.example clinics/demo_clinic/.env
#   set SESSION_SECRET and AGENT_API_TOKEN:  python -c "import secrets; print(secrets.token_urlsafe(48))"
#   no Docker? set DATABASE_URL=sqlite+aiosqlite:///var/dev.db

python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -e ".[backend,agent,dev]"

cd dashboard && npm install && cd ..
```

**Windows with Smart App Control:** it can block SQLAlchemy's compiled speed-ups
(`DLL load failed ... An Application Control policy has blocked this file`). Reinstall it as
plain Python; it's a little slower, which doesn't matter here:

```powershell
$env:DISABLE_SQLALCHEMY_CEXT = "1"
pip install --force-reinstall --no-deps --no-binary sqlalchemy "sqlalchemy==2.1.8"
```

It also blocks LiveKit's native library (`livekit_ffi.dll`), which has no pure-Python fallback,
so on such a machine **run the voice agent in Docker** (`--profile agent`, below). It can block
`.venv\Scripts\*.exe` launchers too: use `python -m alembic` instead of `alembic`.

## Run locally (no Docker)

Run everything from the repo root.

```bash
python -m backend.cli migrate
python -m backend.cli create-staff --email you@example.com --name "Front Desk" --admin
python -m backend.cli seed-demo          # optional: 5 FAKE calls to look at

uvicorn backend.main:create_app --factory --reload      # http://localhost:8000/health
cd dashboard && npm run dev                              # http://localhost:3000
python -m core.agent dev                                 # needs vendor keys; see docs/phase1-testing.md
```

## Run with Docker

```bash
docker compose -f deploy/docker-compose.yml up --build                   # postgres + backend + dashboard
docker compose -f deploy/docker-compose.yml --profile agent up --build   # + the voice agent
```

The backend container runs migrations on start. Create a staff user with
`docker compose -f deploy/docker-compose.yml exec backend python -m backend.cli create-staff ...`.

## Checks

```bash
python -m core.check_env                  # clinic.yaml + every key (format only)
python -m core.check_env --live           # also proves each key against the vendor
pytest                                    # full suite, no keys needed
python -m tests.scenarios.live_text --write   # the scenarios against the REAL LLM (text mode, costs cents)
# Real voice calls: a synthetic caller joins LiveKit and talks to the running agent
docker compose -f deploy/docker-compose.yml --profile agent --profile test run --rm voicetest --write
python -m tests.scenarios.simulate_call --email ... --password ...   # hang-up -> dashboard timing
python -m backend.cli latency-report --write                          # median latency per leg
```

## How the safety rules are enforced

All of these are in code and covered by tests; the prompt repeats them, but nothing relies on it.

- **Emergency / crisis:** every caller message (spoken *or* typed) is screened before the LLM sees it.
  A match plays a fixed 9-1-1 (or 9-8-8) message, never reaches the LLM, flags the call and alerts
  staff immediately. `core/guardrails.py`, `tests/test_guardrails.py`, `tests/test_agent_session.py`.
- **No medical advice:** clinical questions get a reminder injected for the LLM. Every reply is
  filtered sentence by sentence before TTS; advice is replaced with a safe redirect.
- **Read-back:** `book_appointment`, `cancel_appointment` and `take_message` refuse to run until the
  agent has read the number (and date/time) back and the caller said yes. This is checked against
  the conversation itself.
- **Tool reliability:** payloads are validated. On the first error the LLM gets a correction; on the
  second it's told to take a message. If the backend is down, messages are kept and spooled to
  `var/spool/` (`python -m core.backend_client replay`).
- **Disclosure:** `clinic.yaml` won't load unless the greeting mentions AI and recording, and the
  greeting can't be interrupted.
- **Limits:** silence (re-prompt, then hang up), maximum turns and maximum call length come from `clinic.yaml`.

## Adding a clinic

1. Copy `clinics/demo_clinic/` to `clinics/<new_clinic>/`, edit `clinic.yaml` (set `clinic_id` to the folder name).
2. Create its `.env` from `.env.example`.
3. Run with `CLINIC=<new_clinic>`. No code changes (`tests/test_second_clinic.py`).

## Phase 1 status

| Exit criterion | Status |
| --- | --- |
| Browser call books an appointment; on dashboard within 1 s of hang-up | Booking works end to end with the real LLM (text mode); backend → dashboard ~10 ms. **One real voice call still needed (LiveKit secret).** |
| Emergency → 911 response every time, 10 attempts | 10/10 in code tests, and 10/10 with the real LLM across 10 different phrasings. Re-check once by voice. |
| No medical advice in any scripted scenario | None in 22 real-LLM scenarios, 4 of them advice bait ([reports](docs/scenario-runs/)). Re-check once by voice. |
| Median reply latency under 1.5 s, written down | LLM alone: 0.61 s median to first token (`qwen/qwen3.5-9b`). **Full voice latency needs a real call.** |
| Second clinic runs with no code change | Passes (`tests/test_second_clinic.py`). |
