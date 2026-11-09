---
name: roadmap
description: Build plan for Phases 2–5 of the clinic voice receptionist (telephony, booking integration, hardening/privacy/compliance, Canadian deployment and soft launch) with each phase's steps and exit criteria, plus open questions, risks, cost model and the go-live checklist. Use when planning or starting any phase after Phase 1, or when asked about costs, risks, compliance or launch readiness.
---

# Roadmap: Phases 2–5 and launch reference

Moved word for word from `CLAUDE.md` (section 8 Phases 2–5, sections 9–12). The rules in `CLAUDE.md`
still apply, above all: do not start Phase 2 before Phase 1's exit criteria pass.

## Build phases 2–5

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
| Hosted Qwen LLM (paid tier in prod) | $1–5 |
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
