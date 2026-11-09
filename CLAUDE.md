# AI Clinic Voice Receptionist — Build Brief

---

## 1. What we are building

An AI phone receptionist for a medical clinic in **Calgary, Alberta, Canada**.

A patient calls the clinic's number. The AI answers, understands speech, replies in a natural voice, and can:

- Answer FAQs (hours, location, services, parking, insurance)
- Book, reschedule or cancel appointments
- Take messages for the front desk
- Transfer urgent or clinical calls to a human, or tell callers to dial 911 in an emergency

**Model:** one dedicated agent per clinic (single-tenant). Shared core codebase, separate config and deployment per clinic.

**Where:** built and tested from India, deployed to Canada (AWS `ca-west-1`, Calgary).

---

## 2. Locked decisions

| Decision | Choice | Consequence |
| --- | --- | --- |
| First milestone | Full local vertical slice | Agent + backend + Postgres + dashboard all run locally before any cloud or telephony work |
| LLM during build | **Ollama (Qwen 2.5 7B) locally + Groq for timed tests** | Both OpenAI-compatible, so switching between them — and to self-hosted vLLM later — is a `LLM_BASE_URL` change |
| Vendor accounts | None yet | Phase 0 is a signup checklist and gates everything else |
| Voice stack in production | Cloud APIs (Deepgram + Cartesia) | Cheapest to run; requires DPAs, zero-retention and the clinic's privacy sign-off before go-live |

**In scope:** inbound calls, FAQs, booking/reschedule/cancel, message taking, human transfer, 911 handling, call logging, staff dashboard, one clinic deployment.

**Out of scope for now:** outbound calling, SMS conversations, multi-language, self-hosted GPU stack, more than one clinic live. Structure the code so these can be added; do not build them.

---

## 3. Technology stack

| Layer | Choice | Role |
| --- | --- | --- |
| Phone number / carrier | Twilio | Calgary number (403/587/825), carries the call |
| Call bridge | Elastic SIP trunk | Connects the phone network to LiveKit |
| Real-time media | LiveKit Cloud | Streams audio between caller and agent |
| Agent framework | LiveKit Agents (Python) | Logic, turn-taking, tools |
| STT | Deepgram (Nova) | Speech to text, WebSocket streaming |
| LLM | Qwen 2.5 / Qwen 3 (7B–14B) — Ollama locally in dev, Groq for timed tests, vLLM or a paid API in prod | Decides what to say and which tool to call |
| TTS | Cartesia (production default); self-hosted Kokoro for local development and testing | Text to voice. Switching is a `clinic.yaml` change (`voice.tts_provider: cartesia \| kokoro`), never a code change |
| VAD | Silero (built into LiveKit) | Detects start/stop of speech |
| Backend | FastAPI (Python) | Tool endpoints, booking calls, logging |
| Database | PostgreSQL | Call logs, messages, settings |
| Dashboard | Next.js (React) | Front desk staff UI |
| Hosting (prod) | AWS ca-west-1 (Calgary) | Keeps patient data in Canada |
| Booking | Pluggable adapter; manual email/SMS fallback by default | Appointments |

Keep LLM at **7B–14B** for latency. Bigger models add delay on calls.

---

## 4. Architecture

### Call flow

1. **Patient → Twilio:** patient dials the Calgary number, Twilio receives the call over PSTN.
2. **Twilio → LiveKit:** Twilio forwards the call over SIP to the LiveKit SIP endpoint. LiveKit creates a room and adds the caller.
3. **LiveKit → agent:** LiveKit dispatches the Python agent worker into the room. Audio streams both ways over WebRTC.
4. **Agent → Deepgram:** agent streams caller audio to Deepgram over WebSocket, gets live text back.
5. **Agent → LLM:** text + conversation history + clinic system prompt go to the LLM over an OpenAI-compatible API.
6. **LLM → tools:** if the LLM decides to book or check a slot it emits a tool call. The agent runs the function, which calls the FastAPI backend, which calls the booking system.
7. **LLM → TTS:** reply text is streamed to Cartesia over WebSocket. Audio comes back in chunks.
8. **Agent → LiveKit → Twilio → patient:** audio plays to the caller. Target: **under ~1.5 s total response time**.
9. **After the call:** transcript, summary and any messages are saved to PostgreSQL and pushed to the dashboard; urgent calls trigger an email/SMS alert.

### Two paths, keep them separate

- **Voice loop** (Twilio → LiveKit → agent → STT/LLM/TTS → back): must stay under ~1.5 s. **Nothing in this loop waits on the database.**
- **Side channel** (tool calls → FastAPI → booking / Postgres / dashboard / staff alerts): runs after or alongside the reply. The agent never talks to the booking system or the database directly — always through the backend.

### Interfaces

| From | To | Protocol |
| --- | --- | --- |
| Phone network | Twilio | PSTN |
| Twilio | LiveKit | SIP (Elastic SIP Trunk) |
| LiveKit | Agent | WebRTC / LiveKit SDK |
| Agent | Deepgram | WebSocket streaming |
| Agent | LLM | HTTPS, OpenAI-compatible chat completions (streaming) |
| Agent | TTS | WebSocket streaming |
| Agent | Backend | HTTPS REST (JSON) |
| Backend | Booking system | REST API or webhook |
| Backend | Clinic staff | Email (SendGrid/SMTP) or SMS (Twilio) |

---

## 5. Repository layout

One repo, one core codebase, one config file per clinic. Adding a second clinic means adding a folder and a deployment, never a fork.

**Hard rule:** nothing clinic-specific is hard-coded anywhere in `core/` or `backend/`. `clinic.yaml` is the only thing that differs between clinics — name, address, hours, services, FAQ list, transfer number, voice ID, greeting text, booking provider and credentials.

---

## 6. Database schema (PostgreSQL)

Use Alembic for migrations. Encrypt at rest in production. Every staff read or write of a patient record writes an `audit_log` row.

---

## 7. The five LLM tools

The five tools are `check_availability`, `book_appointment`, `cancel_appointment`, `take_message` and `transfer_to_human` (`core/tools.py`). Each one calls the FastAPI backend over HTTP. None of them touch the booking system directly.

**Reliability rules:**

- Validate every tool-call payload against its schema before acting; on a schema failure, retry once with a correction, then fall back to `take_message`.
- Always read back and confirm name, phone number, date and time before booking. Small models get these wrong.

---

## 8. Build phases

### Phase 0 — Accounts and prerequisites

**User creates these (needs their email, card, 2FA):**

| Service | What to get | Cost now |
| --- | --- | --- |
| LiveKit Cloud | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Free tier |
| Deepgram | `DEEPGRAM_API_KEY` | ~$200 free credit |
| Cartesia | `CARTESIA_API_KEY` + a voice ID (needed only when `voice.tts_provider` is `cartesia`) | Free tier |
| Groq | `LLM_API_KEY` + `LLM_BASE_URL` (`https://api.groq.com/openai/v1`) | Free tier, no card |
| Twilio | Account SID + auth token (number comes in Phase 2) | Trial credit (~$15) |
| SendGrid or Twilio SMS | Staff notifications | Free tier |

**TTS during development:** self-hosted **Kokoro** (Kokoro-FastAPI, the `kokoro` service in `docker-compose.yml`, OpenAI-compatible at `http://localhost:8880/v1`) is the local/testing voice, so testing doesn't burn Cartesia credit. Cartesia stays the production default and its adapter stays live. Only the selected provider's settings are required at startup. Locally Kokoro runs on the CPU (the 8 GB GPU is taken by the Qwen model); the GPU image is for the production server.

**Local tooling:** Python 3.11+, Node.js 20+, Docker Desktop, Git, and **Ollama** with `ollama pull qwen2.5:7b` (~5 GB download, needs ~8 GB free RAM).

**LLM provider strategy during development:**

| Use | Provider | `LLM_BASE_URL` | Why |
| --- | --- | --- | --- |
| Everyday iteration | Ollama (local) | `http://localhost:11434/v1` | Free, no rate limits, offline, no data leaves the machine |
| Latency-realistic test passes | Groq | `https://api.groq.com/openai/v1` | Fast enough to reflect production timing |
| Production | Paid API or self-hosted vLLM | varies | Free tiers have no SLA and may train on inputs |

Groq's free tier is **8,000 tokens per minute**. A voice call resends the whole history each turn, so one 3-minute call can run 15–30K tokens and rate-limit itself mid-call. Use Groq for short, timed scenarios; use Ollama for everything else. Both are OpenAI-compatible, so only `LLM_BASE_URL` and `LLM_API_KEY` change — `core/providers/llm.py` must make that switch a config change, never a code change.

Avoid Cerebras: it now requires a payment method even for its trial credit, and has no permanent free tier. OpenRouter's `:free` models work at $0 but are capped at 50 requests/day until $10 of lifetime credit is purchased.

**Claude Code does:** scaffold the repo tree, `.env.example`, `clinic.yaml` for a fictional demo clinic, `docker-compose.yml`, README with run instructions.

**Exit:** `docker compose up` starts Postgres, backend health check returns 200, every key present and validated at startup.

---

### Phase 1 — Full local vertical slice

The whole system on one laptop, end to end, no phone number, no cloud. A browser call produces a logged call that appears live on the dashboard.

- **1.1 Config layer** — `clinic.yaml` pydantic schema, loader, validation errors at startup not mid-call. Demo clinic with realistic fictional hours, services and FAQs.
- **1.2 Database** — Postgres in Docker, the four tables above, Alembic migrations.
- **1.3 FastAPI backend** — `POST /calls`, `GET /calls` with filters, `PATCH /calls/{id}` (status, notes, assignment), `/appointments`, `/auth` with session cookies, `/ws` WebSocket pushing new calls to the dashboard. Every write hits `audit_log`.
- **1.4 Booking adapter** — `BookingProvider` interface: `check_availability`, `create_request`, `cancel_request`. Ship `manual.py` first: records the request, emails/SMSes the front desk for confirmation.
- **1.5 The agent** — LiveKit `AgentSession` with Deepgram STT, OpenAI plugin pointed at `LLM_BASE_URL` (Ollama locally, Groq for timed runs), Cartesia TTS, Silero VAD. System prompt assembled from `clinic.yaml`. **AI disclosure and recording notice in the greeting from the first build**, not bolted on later.
- **1.6 The five tools** — as specified in section 7.
- **1.7 Guardrails** — prompt rules *plus* code checks: never give medical advice; route clinical questions to staff; detect emergency language and tell the caller to hang up and dial 911; hard limit on conversation turns; graceful behaviour on silence and on tool failure.
- **1.8 Dashboard** — Next.js: login, call list (newest first, filters for New / Urgent / Today), call detail with summary + transcript + requested appointment, actions to mark handled / add note / assign, live WebSocket updates with a sound.
- **1.9 Test in the LiveKit Playground** — free, browser-based, no phone number. Scripted scenarios: simple FAQ, booking, cancellation, caller interrupting, background noise, silence, an emergency, a wrong number, an unanswerable question. Log measured latency per reply.

**Exit criteria:**

- [ ] A browser call books an appointment; the request appears in Postgres and on the dashboard within a second of hang-up
- [ ] Emergency phrasing produces the 911 response, every time, across 10 attempts
- [ ] No medical advice in any scripted scenario
- [ ] Median reply latency under 1.5 s, measured and written down
- [ ] A second clinic folder can be added and run with no code change

---

### Phases 2–5 (details in the `roadmap` skill)

- **Phase 2 — Telephony:** Calgary number in Twilio → Elastic SIP trunk → LiveKit inbound trunk and dispatch rule; caller ID from the SIP `From` header; real-call test pass.
- **Phase 3 — Booking integration:** blocked until the clinic names its booking system; `manual.py` keeps bookings working meanwhile.
- **Phase 4 — Hardening, privacy and compliance:** never dead air (fallback to the clinic line), rate limiting, encryption at rest, retention, audit coverage, plus the vendor and clinic paperwork.
- **Phase 5 — Canadian deployment and soft launch:** AWS `ca-west-1`, staged rollout (after-hours → overflow → all calls).

**Before go-live (non-negotiable):** cloud voice APIs send patient audio and transcripts outside Canada, so the clinic must accept that **explicitly and in writing**, with DPAs signed and the clinic's Privacy Impact Assessment signed off by their privacy officer.

Each phase's steps and exit criteria, the open questions, risks, cost model and the go-live checklist are in `.claude/skills/roadmap/SKILL.md`. Read it before starting any phase from 2 on.

---

## 13. Working conventions for Claude Code

- **Build in phase order.** Do not start Phase 2 before Phase 1's exit criteria pass.
- **Never hard-code clinic data.** It goes in `clinic.yaml`.
- **Never let the agent call the booking system or database directly.** Always through the FastAPI backend.
- **Guardrails are code, not just prompt text.** Emergency detection and the no-medical-advice rule must have tests.
- **Use fake patient data only** during development and testing.
- **Secrets never get committed.** `.env` files are gitignored; `.env.example` is committed.
- **Measure latency from the start.** Log per-leg timings (STT, LLM, TTS) on every turn.
- Write tests alongside each phase; the scripted call scenarios in 1.9 are the regression suite.

