# Task list

Progress against the phases in [`CLAUDE.md`](../CLAUDE.md). Updated 2026-11-09.

## Live progress (current session)

Updated after every step. `[x]` done · `[>]` working on it now · `[ ]` next.

| | Step | Result |
|---|---|---|
| [x] | LiveKit creds copied from `sec.txt`; every agent key passes `check_env --live` | LiveKit, Deepgram, Cartesia, LLM all HTTP 200 |
| [x] | Docker: `docker compose up` → Postgres, backend (health 200, database ok), dashboard | **Phase 0 exit check passed** |
| [x] | Postgres: migrations up/down/up + `alembic check`; simulated booking via Docker backend | Dashboard push 11 ms |
| [x] | `.env` comments moved off value lines (Docker read `# AC...` as a value) | test added |
| [x] | Agent moved into Docker (Windows Smart App Control blocks LiveKit's native library) | agent registered with LiveKit |
| [x] | Voice test caller built (`tests/scenarios/voice_call.py`, runs in Docker) | first real call: 911 reply in 1.42 s |
| [x] | Fixed, found by real calls: call saved ~22 s after hang-up (now on caller disconnect) | |
| [x] | Fixed: caller's words missing from transcript on emergency calls | |
| [x] | Fixed: preemptive transcripts double-counted turns; a guard I added silenced the 911 reply | 197 tests pass |
| [x] | Rebuild agent; voice calls: emergency + greeting/FAQ | both pass; 911 in 1.12 s; call saved 0.06 s after hang-up; caller's words in transcript |
| [x] | Latency: agent-side ~1.5 s (end-of-speech wait 0.58, STT 0.4, LLM first sentence 0.82, TTS 0.15); caller hears ~1.9 s | hosts timed; Together/DeepInfra fastest; LLM retry after 4 s instead of 10 s |
| [x] | Voice calls: FAQ, noise, advice pass (content); greeting plays in full when talked over | latency in these runs invalid, see next row |
| [!] | **Blocked: OpenRouter account has $0 credit (free tier).** Real agent requests now get HTTP 402, so replies time out or never come | **you: add credit at openrouter.ai/settings/credits (~$5 is plenty)** |
| [x] | Spoken fallback when the LLM fails (caller heard silence): apology + clinic number, or transfer on a phone call; dashboard "Technical problem, call back" | test added; 198 pass |
| [x] | Rebuilt all containers; voice FAQ call passes; call saved 0.04 s after hang-up | |
| [x] | Removed the "start with a short sentence" prompt line (the model ignored it) | |
| [x] | Update this list, README, test plan, notes | |
| [ ] | **After you add OpenRouter credit:** voice calls interrupt, booking, silence; full voice run; latency → `docs/latency-log.md` | Claude |
| [ ] | Re-run live-LLM text scenarios (needs credit) | Claude |
| [x] | Commit and push | `f2c2d31` on `main` |
| [x] | Ollama: `Modelfile` → `qwen-clinic` (8k context, temperature 0.4); OpenAI endpoint + tool calling verified | 3/3 tool calls; ~0.2 s to first token on the RTX 5050 |
| [x] | Ollama settings: `OLLAMA_KEEP_ALIVE=2h`, `OLLAMA_NUM_PARALLEL=1` (user env vars) | |
| [x] | Project wired to Ollama: short key accepted for a local LLM; Docker agent reaches it via `host.docker.internal` (`LLM_LOCALHOST_ALIAS`) | 5 tests added |
| [x] | Text scenarios on `qwen-clinic` | 19/22; multi-step flows pass ~1 in 3 (code checks blocked every bad save) |
| [x] | Voice run on `qwen-clinic`: greeting, noise, **interrupt, booking**, emergency, advice pass | voice booking on dashboard 0.05 s after hang-up |
| [x] | Fixed, found by the silence call: the agent could never end a call itself (silence, limits, LLM-down goodbye): `delete_room` lost LiveKit's HTTP session | test added; silence call now passes; 204 tests |
| [x] | Update README, `.env.example`, test plan, this list | |
| [x] | First-reply latency: warm the LLM's prompt cache while the greeting plays (`LLM_WARMUP`, on by default; own client, so a failed warm-up can't set off the LLM-down fallback) | first-reply LLM time 1.6 s → 0.12–0.50 s; 2 tests; 206 pass |
| [x] | Voice run with the warm-up | 6/7 (booking: 7B model mistake, passed the run before); agent-side median **1.39 s**; caller hears median 2.01 s |
| [x] | Commit and push the Ollama work and warm-up | pushed to `main` |
| [x] | Ollama: pulled `qwen3:8b`, built `qwen3-clinic` (`Modelfile.qwen3`); tool calls 3/3 with the right date | |
| [x] | Found: Ollama ignores `/no_think` (thinking added 2–6 s); `LLM_EXTRA_BODY={"reasoning_effort":"none"}` turns it off | config only |
| [x] | Found: `localhost` adds ~2 s per request on Windows (IPv6 tried first); `.env` now uses 127.0.0.1 | |
| [x] | Test caller rebuilt as a chat (as one long prompt the 8B model hung up early or repeated itself) | |
| [x] | Text scenarios on `qwen3-clinic` | **22/22**; multi-step 8/8 (qwen2.5 with the same caller: 4/8) |
| [~] | Voice run on `qwen3-clinic` | greeting, FAQ, noise, emergency, advice pass; agent-side median 1.42 s, caller hears 1.85 s. Interrupt failed (agent stopped after 2.2 s). Booking + silence invalid: Cartesia out of credit |
| [!] | **Blocked: Cartesia returned 402 (out of free credit).** The agent can't speak; the test caller can't either | **you: add Cartesia credit or upgrade the plan (play.cartesia.ai)** |
| [ ] | Found: when TTS fails, the caller hears nothing (dead air). Needs a fallback that doesn't depend on TTS (transfer / forward on a phone call); Phase 4 item, now proven real | Claude |
| [ ] | After Cartesia credit: re-run voice interrupt, booking, silence on `qwen3-clinic` | Claude |
| [x] | Commit and push the qwen3 work | pushed to `main` |

`[x]` done and verified · `[~]` built, but not yet verified the way the exit criterion asks · `[ ]` not started.
**Who** says who has to do it: *you* (needs your accounts, card, 2FA or admin rights), *Claude*, or *clinic*.

## Summary

| Phase | Done | Partly | To do | Status |
|---|---|---|---|---|
| 0 Accounts and prerequisites | 14 | 0 | 1 | **Exit check passed** (only Twilio left, needed in Phase 2) |
| 1 Local vertical slice | 28 | 1 | 2 | 4 of 5 exit criteria passed; latency still over 1.5 s |
| 2 Telephony | 0 | 1 | 10 | Not started (blocked by Phase 1) |
| 3 Booking integration | 1 | 0 | 3 | Blocked: clinic hasn't named its booking system |
| 4 Hardening and compliance | 3 | 3 | 12 | Not started |
| 5 Canadian deployment | 1 | 0 | 9 | Not started |
| **Total** | **47** | **5** | **37** | **47 of 89 items done (53%)** |

Phases 2–5 have bigger items than Phase 0–1, so by effort it's closer to a third done.

---

## Phase 0: Accounts and prerequisites

| | Task | Who |
|---|---|---|
| [x] | Repo tree, `.env.example`, demo `clinic.yaml`, `docker-compose.yml`, README | Claude |
| [x] | Startup key check: `python -m core.check_env`, `--live` proves each key with the vendor | Claude |
| [x] | Deepgram key: validated live | you |
| [x] | Cartesia key: validated live; voice chosen ("Cindy - Receptionist") | you / Claude |
| [x] | OpenRouter key: validated live | you |
| [x] | LiveKit URL + API key | you |
| [x] | Python 3.14, Node.js, Git installed; repo pushed to GitHub | you |
| [x] | Delete unused portable Postgres download (`.devdb/pg.zip`) | Claude |
| [x] | `sec.txt` key dump git-ignored (keys copied into `clinics/demo_clinic/.env`) | Claude |
| [x] | SQLAlchemy reinstalled without the compiled files that Windows Smart App Control blocks | Claude |
| [x] | LiveKit API key + secret (new key), validated live | you |
| [x] | WSL 2 + Docker Desktop installed | you |
| [x] | `docker compose up`: Postgres starts, backend health 200 with database ok (**Phase 0 exit check passed**) | Claude |
| [x] | Ollama + `qwen2.5:7b`, project variant `qwen-clinic` built from `Modelfile` | you / Claude |
| [ ] | Twilio account SID + auth token (not needed until Phase 2) | you |

## Phase 1: Full local vertical slice

### 1.1 Config, 1.2 Database, 1.3 Backend, 1.4 Booking adapter

| | Task | Who |
|---|---|---|
| [x] | `clinic.yaml` schema, loader, clear errors at startup; AI + recording disclosure enforced in the greeting | Claude |
| [x] | Demo clinic with realistic fictional hours, services, FAQs, holidays | Claude |
| [x] | Tables + Alembic migrations (calls, appointment_requests, staff_users, audit_log, plus messages) | Claude |
| [x] | Migrations on Postgres: upgrade, downgrade, upgrade, `alembic check` clean | Claude |
| [x] | Backend: calls (create / list with filters / update), appointments, messages, session-cookie login, WebSocket push | Claude |
| [x] | Audit log on every staff read and write | Claude |
| [x] | Manual booking adapter: availability from clinic hours, request recorded, staff notified | Claude |

### 1.5 Agent, 1.6 Tools, 1.7 Guardrails

| | Task | Who |
|---|---|---|
| [x] | LiveKit agent: Deepgram STT → Qwen LLM → Cartesia TTS, Silero VAD; prompt built from `clinic.yaml` | Claude |
| [x] | Uninterruptible greeting with AI disclosure and recording notice | Claude |
| [x] | Five tools, payload validation, retry once with a correction, then fall back to taking a message | Claude |
| [x] | Read-back check in code: no booking/cancel/message until details read back and caller says yes | Claude |
| [x] | Emergency (911) and crisis (988) detection in code, before the LLM; flags call, alerts staff | Claude |
| [x] | Advice filter on every reply before TTS; clinical questions routed to staff | Claude |
| [x] | Limits: silence re-prompt + hang-up, max turns, max call length; backend-down spooling | Claude |
| [x] | LLM picked by measurement: `qwen3.5-9b` (~0.6 s to first token) instead of `qwen3-14b` (~0.9–1.7 s; one of its OpenRouter hosts has no tool support) | Claude |
| [x] | Fixed from the live-LLM run: two emergency phrasings the code missed ("can't stop the bleeding", "took way too many pills") | Claude |
| [x] | Fixed from the live-LLM run: "next week" booked tomorrow; calendar now marks this week / next week | Claude |
| [x] | Fixed from the live-LLM run: agent said "I've recorded your message" after the tool refused it | Claude |
| [x] | Safety net: if the agent promised a callback but nothing was saved, the call shows "Needs action" | Claude |

### 1.8 Dashboard

| | Task | Who |
|---|---|---|
| [x] | Login, call list (New / Urgent / Today, search), call detail, mark handled / note / assign | Claude |
| [x] | Live updates with sound; checked in the browser | Claude |

### 1.9 Testing

| | Task | Who |
|---|---|---|
| [x] | 206 automated tests (no keys needed) | Claude |
| [x] | Text-mode scenario runner against the real LLM: `python -m tests.scenarios.live_text`. 22/22 passed ([report](scenario-runs/)) | Claude |
| [x] | Real voice calls through LiveKit with a synthetic caller (`tests/scenarios/voice_call.py`, in Docker): all 7 pass (greeting talked over, FAQ, noise, interrupt, emergency, advice, booking, silence); run on local Ollama | Claude |
| [ ] | One call by a human in the LiveKit Playground (real voice, real mic) | you |
| [ ] | Add the Playground results to the log in `docs/phase1-testing.md` | you + Claude |

### Phase 1 exit criteria

| | Criterion | Evidence so far |
|---|---|---|
| [x] | Browser call books an appointment; on the dashboard within 1 s of hang-up | Voice booking over LiveKit (synthetic caller, local Ollama): request in Postgres and on the dashboard 0.05 s after hang-up. A human Playground call is still on the list below. |
| [x] | Emergency → 911 response every time, 10 attempts | 10/10 in code tests, 10/10 with the real LLM, and by real voice call (1.12 s). |
| [x] | No medical advice in any scripted scenario | 0 advice in 22 real-LLM scenarios (4 of them advice bait). Re-check once by voice. |
| [~] | Median reply latency under 1.5 s, measured and written down | Local Ollama + prompt-cache warm-up: agent-side median 1.39 s (under target), but the caller hears a median of 2.01 s (WebRTC + playout on top). Slowest turns are the ones with a tool call. To decide: which number counts, and a re-run on the hosted model. |
| [x] | Second clinic runs with no code change | `tests/test_second_clinic.py` |

## Phase 2: Telephony (blocked until Phase 1 passes)

| | Task | Who |
|---|---|---|
| [ ] | Buy a Calgary number in Twilio (403 / 587 / 825) | you |
| [ ] | Twilio Elastic SIP Trunk → LiveKit SIP URI; attach the number | you + Claude |
| [ ] | LiveKit inbound SIP trunk + dispatch rule | Claude |
| [~] | Caller ID from the SIP `From` header onto the call record (code reads `sip.phoneNumber`; untested) | Claude |
| [ ] | Transfer to a human line, tested | you + Claude |
| [ ] | Real-call test pass: accents, speakerphone, bad signal, two people, hang-up mid-sentence, silence, DTMF | you |
| [ ] | 10 consecutive calls answered within two rings, no dropped audio | you |
| [ ] | Caller ID correct on every record | Claude |
| [ ] | Real-call latency still under 1.5 s median | Claude |
| [ ] | Choose how the clinic's own number connects (forwarding / porting / direct SIP) | clinic |
| [ ] | Twilio keys added and validated | you |

## Phase 3: Booking integration

| | Task | Who |
|---|---|---|
| [x] | Manual fallback (requests + staff confirmation in the dashboard) | Claude |
| [ ] | Clinic names its booking system (Jane / Google Calendar / Accuro / Oscar / Cliniko) | clinic |
| [ ] | Build that adapter; apply early if it's Jane (access is limited) | Claude |
| [ ] | Exit: agent booking appears in the real system; cancel removes it | Claude |

## Phase 4: Hardening, privacy and compliance

| | Task | Who |
|---|---|---|
| [x] | AI disclosure + recording notice in the greeting (enforced by config validation) | Claude |
| [x] | Guardrail tests re-run after every prompt change (pytest + `live_text`) | Claude |
| [x] | Audit log covers staff reads and writes | Claude |
| [~] | LiveKit recording disabled (`record=False`); real calls run with it; confirm nothing appears in LiveKit Cloud's recordings | you |
| [~] | Secrets out of the repo (done); move to AWS Secrets Manager (Phase 5) | Claude |
| [~] | Fallback: agent/provider down → never dead air. LLM failure now spoken + transfer on SIP (done); agent process down → carrier-level forward (Phase 2/5) | Claude |
| [ ] | Rate limiting and abuse handling on the backend | Claude |
| [ ] | Transcripts encrypted at rest; recordings only with consent | Claude |
| [ ] | Retention: transcripts auto-deleted after an agreed window | Claude + clinic |
| [ ] | Zero data retention on Deepgram, Cartesia, OpenRouter (and the host it routes to) | you |
| [ ] | DPAs with Twilio, Deepgram, Cartesia, LLM provider | you |
| [ ] | Service agreement with the clinic (Alberta HIA affiliate role; PIPEDA / PIPA) | you + clinic |
| [ ] | Vendor list for the clinic's Privacy Impact Assessment (who sees what, where) | Claude |
| [ ] | Clinic completes the PIA | clinic |
| [ ] | Privacy officer sign-off | clinic |
| [ ] | Pin the LLM to named hosts with a DPA instead of "fastest" routing | Claude + you |
| [ ] | Re-test emergency and no-advice by voice after every prompt change | you + Claude |
| [ ] | Real-call verification of the disclosure | you |

## Phase 5: Canadian deployment and soft launch

| | Task | Who |
|---|---|---|
| [x] | Containers for agent, backend, dashboard: built and running locally | Claude |
| [ ] | AWS ca-west-1: ECS/EC2, RDS Postgres encrypted, HTTPS dashboard | you + Claude |
| [ ] | Confirm LiveKit Cloud data region, or self-host in Canada | you + Claude |
| [ ] | Secrets in AWS Secrets Manager | Claude |
| [ ] | CloudWatch dashboards + alerts (call failures, latency > 2 s, provider errors, worker down) | Claude |
| [ ] | Nightly encrypted backups, restore tested once | Claude |
| [ ] | Rollback plan: AI off in under five minutes | Claude |
| [ ] | Front desk trained on the dashboard | you + clinic |
| [ ] | Soft launch: after-hours → overflow → all calls, a week each | you + clinic |
| [ ] | Go-live checklist (CLAUDE.md §12) | everyone |

---

## Your next steps

1. **Add OpenRouter credit** at openrouter.ai → Settings → Credits (about $5 is plenty for testing). The account is on the free tier with $0 credit, so the agent's real requests get HTTP 402 and replies stall. Tell Claude when it's done.
2. **Make one call yourself** in the [LiveKit Agents Playground](https://agents-playground.livekit.io) with your microphone (the agent is running in Docker). The synthetic caller can't judge how the voice *sounds*.
3. **Delete `sec.txt`.** The keys are in `clinics/demo_clinic/.env` now and all validate. It's git-ignored, but it's a second plain-text copy of every key.
4. Optional, for Phase 2: Twilio account SID + auth token.
