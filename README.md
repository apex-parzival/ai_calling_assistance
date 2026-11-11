# AI Clinic Voice Receptionist

AI phone receptionist for a medical clinic in Calgary, Alberta. One shared codebase,
one `clinics/<name>/` folder per clinic. See [`CLAUDE.md`](CLAUDE.md) for the full build brief.

**Status (2026-11-11):** Phase 1 complete: all five exit criteria met, including a median reply
time of 1.21 s on real voice calls. Phase 2 (telephony) is next. See [Phase 1 status](#phase-1-status),
the [task list](docs/TASKS.md), the [runbook](docs/RUNBOOK.md) (every command, and how to test the
whole system) and the Build & Deployment Guide (v2) PDF (what changes for production).

**Current call flow: intake only (client request).** The agent greets the caller, asks whether they have a
health question or want an appointment (and for which service), takes their name and callback number,
says the front desk will call back (`callback_message` in `clinic.yaml`) and ends the call. The dashboard
lists caller name, phone number, reason and summary. The full receptionist (booking, cancelling,
messages, transfers, FAQs) is commented out, not deleted; `core/agent.py` says how to switch back.

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
| [Cartesia](https://play.cartesia.ai) | API key + pick a voice. Production TTS; only needed when `voice.tts_provider: cartesia` | `CARTESIA_API_KEY`; voice ID goes in `clinic.yaml` → `voice.voice_id` |
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

## Local LLM with Ollama (optional)

Free, offline, no rate limits. Needs [Ollama](https://ollama.com) and about 5 GB of disk.

```powershell
ollama pull qwen3:8b
ollama create qwen3-clinic -f Modelfile.qwen3   # 8k context, low temperature
```

Ollama defaults to a 2,048-token context, which cuts off the system prompt, and its OpenAI API
can't change it: that's what the `Modelfile` variant is for. Then set the four Ollama lines shown in
`.env.example` (`LLM_BASE_URL=http://127.0.0.1:11434/v1`, `LLM_MODEL=qwen3-clinic`,
`LLM_EXTRA_BODY={"reasoning_effort":"none"}`). Two traps: on Windows, `localhost` adds ~2 s per
request (IPv6 is tried first), so use `127.0.0.1`; and Ollama ignores Qwen3's `/no_think`, so
thinking stays on (2–6 s per reply) unless `reasoning_effort` is `none`.
The Docker agent reaches it automatically (`LLM_LOCALHOST_ALIAS` in `docker-compose.yml`).
Optional user environment variables: `OLLAMA_KEEP_ALIVE=2h` (no 10–30 s reload after a break) and
`OLLAMA_NUM_PARALLEL=1` (parallel slots split the context).

Measured on an RTX 5050 laptop: `qwen3-clinic` passed 22/22 text scenarios and 8/8 multi-step
runs (booking, cancel, message, transfer), ~0.3 s to first token. `qwen2.5:7b` (`Modelfile`,
`qwen-clinic`) is a little faster but completed the multi-step runs only 4 times in 8.

## Groq for timed tests

`clinics/demo_clinic/.env` keeps a commented-out Groq block next to the Ollama one; swap which is
active (see [runbook](docs/RUNBOOK.md) §1.2). Groq serves no 7–14B Qwen today; `qwen/qwen3.8-27b`
measured 0.29 s to first token. Its free tier allows ~7,000 input tokens per minute and every
request resends the ~2,500-token prompt, so a call past about two turns gets HTTP 429 (the agent
then plays its "technical problem" fallback). Use it for short timed runs, spaced out
(`voicetest --pause 45`); use Ollama for everything else.

## Text to speech: Kokoro locally, Cartesia in production

`clinic.yaml` → `voice.tts_provider` picks the voice; switching is a config change only.

| | Kokoro (local / testing) | Cartesia (production default) |
|---|---|---|
| `clinic.yaml` | `tts_provider: kokoro`, `voice_id: af_heart` | `tts_provider: cartesia`, `voice_id: <Cartesia voice ID>` |
| `.env` | `KOKORO_BASE_URL` (default `http://localhost:8880/v1`), `KOKORO_VOICE` (used if no `voice_id`) | `CARTESIA_API_KEY` |
| Runs | `kokoro` service in `docker-compose.yml` (starts with `--profile agent`), CPU image | Cartesia cloud API |

The demo clinic currently uses Kokoro; its Cartesia voice ID is kept in a comment in `clinic.yaml`.
Only the selected provider's settings are required: the agent and `check_env` name the missing
variable if they aren't set. Inside docker compose the agent reaches Kokoro at `http://kokoro:8880/v1`.
The base compose file runs Kokoro on the CPU; the GPU override below is what the timed runs used.

The voice test caller (`voicetest`) speaks its own lines through Kokoro too (voice `am_michael`),
so voice tests need no Cartesia credit; `--caller-tts cartesia` switches it back.

Measured 2026-11-09 on the dev laptop's CPU: Kokoro takes 0.7–4 s to first audio (Cartesia
~0.15 s), with pauses of over a second between sentences of a reply. Good for testing what the
agent says, not for measuring latency.

**Kokoro on an NVIDIA GPU** (0.12–0.5 s to first audio, 760 MB VRAM):

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.kokoro-gpu.yml --profile agent up -d --build kokoro
```

It builds `deploy/Dockerfile.kokoro-gpu`, which swaps in a CUDA 12.8 PyTorch: the upstream GPU image
fails on RTX 50xx (Blackwell) cards with "no kernel image is available". On an 8 GB laptop GPU it
fits next to `qwen3-clinic`, but the two share the GPU, so the LLM gets slower.

## Turn-taking

`clinic.yaml` → `voice.end_of_turn_silence` (seconds, default 0.55) is how long the caller must be
silent before the agent answers. It sets both Silero's silence window and LiveKit's endpointing
delay; Silero's window was the real floor, so changing LiveKit's alone did nothing. The demo clinic
uses 0.4. Lower answers sooner but can cut in when a caller pauses mid-sentence (reading out a
number); re-tune it on real phone calls.

When a tool starts and the model hasn't said anything yet, the agent says a short hold-on line
("Let me check that for you") while the answer is generated, so the caller isn't left in silence.

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
#   on Groq's free tier add --pause 45 (seconds between scenarios)
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
| Browser call books an appointment; on dashboard within 1 s of hang-up | **Met.** Voice booking over LiveKit: on the dashboard 0.05 s after hang-up. Human Playground call works (2026-11-11). |
| Emergency → 911 response every time, 10 attempts | **Met.** 10/10 in code tests, 10/10 with the real LLM across 10 phrasings, and by voice. |
| No medical advice in any scripted scenario | **Met.** None in 22 real-LLM scenarios (4 of them advice bait) or in voice runs ([reports](docs/scenario-runs/)). |
| Median reply latency under 1.5 s, written down | **Met.** Groq + Kokoro on the GPU: caller hears median 1.21 s, agent-side 1.08 s ([report](docs/scenario-runs/2026-11-11-144240-voice.md), [log](docs/phase1-testing.md#results-log)). Re-measure on the production LLM and real phone calls. |
| Second clinic runs with no code change | **Met** (`tests/test_second_clinic.py`). |
