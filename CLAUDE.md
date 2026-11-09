# AI Clinic Voice Receptionist — Build Brief

> Paste this into Claude Code (or keep it as `CLAUDE.md` at the project root so it loads automatically).
> Project root: `D:\projects\ranga sir\agentic assistance`

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
| LLM during build | Hosted Qwen via Groq or OpenRouter | OpenAI-compatible, so swapping to self-hosted vLLM later is a `base_url` change |
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
| LLM | Qwen 2.5 / Qwen 3 (7B–14B) via Groq or OpenRouter | Decides what to say and which tool to call |
| TTS | Cartesia | Text to voice, WebSocket streaming |
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

```
agentic assistance/
├─ core/
│  ├─ agent.py            # LiveKit AgentSession: STT → LLM → TTS wiring
│  ├─ prompts.py          # system prompt built from clinic config
│  ├─ tools.py            # the 5 LLM function tools
│  ├─ config.py           # loads + validates clinic.yaml (pydantic)
│  └─ providers/          # swappable adapters
│     ├─ stt.py           # deepgram | faster-whisper
│     ├─ tts.py           # cartesia | elevenlabs | kokoro
│     └─ llm.py           # any OpenAI-compatible base_url
├─ backend/
│  ├─ main.py             # FastAPI app
│  ├─ routes/             # /calls, /appointments, /messages, /auth, /ws
│  ├─ models.py           # SQLAlchemy models
│  ├─ booking/
│  │  ├─ base.py          # BookingProvider interface
│  │  ├─ manual.py        # email/SMS fallback (default)
│  │  └─ jane.py          # filled in once access is confirmed
│  └─ migrations/         # alembic
├─ dashboard/             # Next.js staff front desk
├─ clinics/
│  └─ demo_clinic/
│     ├─ clinic.yaml      # hours, services, FAQs, transfer number, voice
│     └─ .env             # secrets, never committed
├─ deploy/
│  ├─ Dockerfile.agent
│  ├─ Dockerfile.backend
│  ├─ docker-compose.yml  # local: postgres + backend + agent + dashboard
│  └─ aws/                # phase 5
├─ tests/
└─ docs/
```

**Hard rule:** nothing clinic-specific is hard-coded anywhere in `core/` or `backend/`. `clinic.yaml` is the only thing that differs between clinics — name, address, hours, services, FAQ list, transfer number, voice ID, greeting text, booking provider and credentials.

---

## 6. Database schema (PostgreSQL)

```sql
calls                -- id, caller_name, caller_phone, started_at, duration,
                     -- reason, urgency, summary, transcript, recording_url,
                     -- status (New | In progress | Handled)
appointment_requests -- id, call_id, requested_date, service, booking_status
staff_users          -- id, name, email, password_hash, role
audit_log            -- who viewed or changed which record and when
```

Use Alembic for migrations. Encrypt at rest in production. Every staff read or write of a patient record writes an `audit_log` row.

---

## 7. The five LLM tools

```python
check_availability(date, service)
book_appointment(name, phone, date, time, service)
cancel_appointment(phone, date)
take_message(name, phone, reason)
transfer_to_human()
```

Each one calls the FastAPI backend over HTTP. None of them touch the booking system directly.

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
| Cartesia | `CARTESIA_API_KEY` + a voice ID | Free tier |
| Groq or OpenRouter | `LLM_API_KEY` + `LLM_BASE_URL` | Free tier / cents |
| Twilio | Account SID + auth token (number comes in Phase 2) | Trial credit (~$15) |
| SendGrid or Twilio SMS | Staff notifications | Free tier |

**Local tooling:** Python 3.11+, Node.js 20+, Docker Desktop, Git.

**Claude Code does:** scaffold the repo tree, `.env.example`, `clinic.yaml` for a fictional demo clinic, `docker-compose.yml`, README with run instructions.

**Exit:** `docker compose up` starts Postgres, backend health check returns 200, every key present and validated at startup.

---

### Phase 1 — Full local vertical slice

The whole system on one laptop, end to end, no phone number, no cloud. A browser call produces a logged call that appears live on the dashboard.

- **1.1 Config layer** — `clinic.yaml` pydantic schema, loader, validation errors at startup not mid-call. Demo clinic with realistic fictional hours, services and FAQs.
- **1.2 Database** — Postgres in Docker, the four tables above, Alembic migrations.
- **1.3 FastAPI backend** — `POST /calls`, `GET /calls` with filters, `PATCH /calls/{id}` (status, notes, assignment), `/appointments`, `/auth` with session cookies, `/ws` WebSocket pushing new calls to the dashboard. Every write hits `audit_log`.
- **1.4 Booking adapter** — `BookingProvider` interface: `check_availability`, `create_request`, `cancel_request`. Ship `manual.py` first: records the request, emails/SMSes the front desk for confirmation.
- **1.5 The agent** — LiveKit `AgentSession` with Deepgram STT, OpenAI plugin pointed at Groq/OpenRouter, Cartesia TTS, Silero VAD. System prompt assembled from `clinic.yaml`. **AI disclosure and recording notice in the greeting from the first build**, not bolted on later.
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

### Phase 2 — Telephony

1. Buy a Calgary number in Twilio (403, 587 or 825).
2. Create an Elastic SIP Trunk, origination URI pointed at the LiveKit SIP URI.
3. Attach the number to the trunk.
4. In LiveKit, create the inbound SIP trunk and a dispatch rule that puts callers into a room with the agent.
5. Read the caller's real number from the SIP `From` header and store it on the call record, so forwarded calls still show the patient's number.
6. Call the number from India over a SIP softphone (Zoiper) or Twilio Voice in the browser, to avoid international charges.

**Real-call test pass:** different accents, speakerphone, bad signal, two people talking, caller hangs up mid-sentence, caller says nothing, DTMF noise, transfer to a human line.

**Exit criteria:**

- [ ] Ten consecutive calls answered within two rings, no dropped audio
- [ ] Caller ID correct on every call record
- [ ] Transfer to a human line works and the caller hears what is happening
- [ ] Latency on a real call still under ~1.5 s median

**Connecting the clinic's existing number** (pick one once the provider is known):

| Option | How | Extra cost | Setup time | Best when |
| --- | --- | --- | --- | --- |
| Call forwarding | Clinic's provider forwards to a hidden Twilio number wired to LiveKit | ~$1–2/mo + per-minute | Same day | Landline or any provider; quickest start |
| Port the number | Number moves to Twilio and goes straight to LiveKit over SIP | Usually free | 1–3 weeks | Full control long term |
| Direct SIP | Clinic's VoIP (RingCentral, Telus Business Connect) sends calls over SIP to LiveKit | None | 1–3 days | Clinic already uses SIP-capable VoIP |

Forwarding modes: **always** / **busy or no answer** (e.g. after 4 rings) / **after hours**.

---

### Phase 3 — Booking integration

Blocked until the clinic names its system. Do not wait on it — `manual.py` from 1.4 already gives working bookings via staff confirmation.

| Path | When | Work |
| --- | --- | --- |
| Manual fallback | Already built in 1.4 | Request recorded, email/SMS to front desk, staff confirm in dashboard |
| Google Calendar | If the clinic shares one | Service account, free/busy read, event create — ~2 days |
| Jane App | If API or partner access is granted | **Apply early**, access is limited |
| Accuro / Oscar / Cliniko | If that is what they use | Each has a REST API; scope once known |

**Exit:** an appointment booked by the agent appears in the clinic's real system (or a confirmed staff inbox), and a cancellation removes it.

---

### Phase 4 — Hardening, privacy and compliance

Cloud voice APIs mean patient audio and transcripts leave Canada. Workable, but the clinic must accept it **explicitly and in writing before go-live**.

**Technical:**

- [ ] AI disclosure and recording notice in the greeting, verified on a real call
- [ ] Emergency and no-medical-advice guardrails re-tested after every prompt change
- [ ] Fallback: if the agent or any provider is down, the call forwards to the clinic line or voicemail — a caller must never hear dead air
- [ ] Rate limiting and abuse handling on the backend
- [ ] Transcripts encrypted at rest; recordings stored only with consent
- [ ] Audit log covers every read and write of a patient record by staff
- [ ] Secrets in AWS Secrets Manager, never in the repo or image
- [ ] Data retention policy implemented: transcripts auto-deleted after an agreed window

**Paperwork (not code):**

- [ ] Zero data retention enabled on Deepgram, Cartesia and the LLM provider where offered
- [ ] DPAs signed with Twilio, Deepgram, Cartesia and the LLM provider
- [ ] Written service agreement with the clinic covering data handling, and your role as their affiliate under Alberta's **Health Information Act (HIA)**. PIPEDA / PIPA (Alberta) may also apply.
- [ ] Clinic completes a Privacy Impact Assessment with their privacy officer, covering every US vendor in the chain **including Twilio**
- [ ] Clinic's privacy officer or legal adviser signs off on the final setup

*Not legal advice — the clinic's privacy officer confirms the arrangement. Our job is to hand them an accurate list of every vendor, what data each sees, and where it is processed.*

---

### Phase 5 — Canadian deployment and soft launch

1. Containerise agent, backend and dashboard; one compose or ECS task definition per clinic.
2. Deploy to AWS **ca-west-1 (Calgary)**: ECS or small EC2 for agent + backend, RDS PostgreSQL encrypted at rest, ALB/CloudFront with HTTPS for the dashboard.
3. Confirm the LiveKit Cloud data region, or self-host LiveKit in the same Canadian region.
4. Secrets in AWS Secrets Manager; nothing in env files on the box.
5. CloudWatch dashboards and alerts: call failures, latency over 2 s, provider errors, agent worker down.
6. Nightly encrypted RDS backups, tested by restoring once.
7. Staged rollout.

**Soft launch order:**

| Stage | What the AI takes | Hold for |
| --- | --- | --- |
| 1 | After-hours calls only | One week, review every transcript |
| 2 | Overflow — calls staff do not answer in four rings | One week, review daily |
| 3 | All calls | Ongoing, weekly review |

Advance a stage only when the previous week produced no mishandled clinical call and no front-desk complaint.

---

## 9. Open questions / blockers

None of these block Phase 1.

**From the developer:** vendor keys (blocks Phase 1 testing); budget ceiling per clinic per month; who owns the production AWS account; is a real clinic signed or is this a pitch build.

**From the clinic — booking:** which booking software; can they grant API access, or is a shared calendar realistic.

**From the clinic — phone:** who is the provider (Telus, Shaw/Rogers, Bell, RingCentral, other); landline or VoIP; should the AI take all calls / only missed / only after-hours; which number do urgent calls transfer to and who answers it; who has admin access to the phone account.

**From the clinic — content and policy:** hours, services, parking, insurance and FAQ answers in their own words; what counts as urgent and what must always reach a human; do they accept US vendors processing patient audio, and who signs off.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Clinic's privacy officer rejects US vendors late | Medium | High — forces a voice-stack rebuild | Raise in week 1; keep STT/TTS behind adapters so Kokoro + faster-whisper stay a config swap |
| Jane App API access denied or slow | High | Medium — bookings stay manual | Manual adapter ships in Phase 1, so this degrades rather than blocks |
| Latency over 1.5 s on real calls | Medium | High — callers talk over the AI | Measure per leg from Phase 1; stream everything; LLM at 7–14B; nearest provider region |
| Agent mishandles a clinical or emergency call | Low | Severe | Hard guardrails + staged rollout + daily transcript review during soft launch |
| Qwen function-calling unreliable on edge cases | Medium | Medium | Schema validation, retry with correction, fall back to taking a message |
| Telephony fails silently at 2am | Medium | High | Health checks, CloudWatch alerts, carrier-level fallback to the clinic line |
| Small-model accuracy on names and phone numbers | High | Medium | Read back and confirm every name, number and time before acting |

---

## 11. Cost model (USD, approximate)

**Build and testing phase (India):** ~**$0–20 total** — LiveKit free tier, Deepgram ~$200 credit, Cartesia free tier, hosted Qwen at cents, Twilio trial credit.

**Production, per clinic, per month, at 1,000 call minutes:**

| Item | Monthly |
| --- | --- |
| Twilio number + inbound voice/SIP | $6–12 |
| LiveKit Cloud | ~$10 |
| Deepgram STT | $4–8 |
| Cartesia TTS | $20–80 |
| Hosted Qwen LLM | $1–5 |
| Agent + backend on AWS | $20–60 |
| RDS PostgreSQL (small) | $15–30 |
| **Total** | **$80–200** |

TTS is the largest variable cost and scales with call minutes. Self-hosted Kokoro would cut it to near zero at the price of a GPU server ($300–800/mo), which only pays off across several clinics.

**Client pricing:** one-time setup fee for build + integration + testing, plus a monthly fee covering usage, maintenance and support, set comfortably above running cost.

---

## 12. Go-live checklist

- [ ] AI disclosure and recording notice in the greeting, heard on a real call
- [ ] Emergency handling (911) tested on live calls, not just the Playground
- [ ] Human transfer tested to the clinic's real urgent number
- [ ] No medical advice, verified across the scripted scenario set
- [ ] Patient data stored in Canada and encrypted at rest
- [ ] Agreements signed with the clinic and every vendor
- [ ] Clinic's Privacy Impact Assessment complete and signed off
- [ ] Median latency under 1.5 s on real calls
- [ ] Call logs, dashboard and staff notifications working
- [ ] Monitoring and alerts live, with someone on the other end of them
- [ ] Fallback tested: agent killed mid-call, calls still reach the clinic
- [ ] Front desk trained on the dashboard
- [ ] Rollback plan written: how to turn the AI off in under five minutes

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

### Suggested first command

> Read `CLAUDE.md`. Start Phase 0: scaffold the repo tree exactly as described in section 5, write `.env.example`, a `clinic.yaml` for a fictional demo clinic in Calgary, `docker-compose.yml` for local Postgres + backend, and a README with run instructions. Do not write agent or dashboard code yet.
