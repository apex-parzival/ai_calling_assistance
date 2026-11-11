# Runbook: run and test the whole system locally

Everything here runs on the dev laptop (Windows 11, PowerShell). Unless a step says otherwise,
open **PowerShell in the repo root**:

```powershell
cd "D:\projects\ranga sir\agentic assistance"
.venv\Scripts\activate
```

## Ground rules (apply to every step)

- **Fake patient data only.** Fictional names and `403-555-01xx` numbers. Never a real patient, never
  your own health details. Patient audio goes to US vendors (LiveKit, Deepgram, Groq); that's only
  allowed for real patients after the clinic's privacy officer signs off in writing (Phase 4/5).
- **Secrets stay local.** Keys live only in `clinics/demo_clinic/.env` (gitignored). Never paste a key
  into a commit, a chat, a screenshot or a URL. `sec.txt` is gitignored too, but every key in it is
  now in `.env`: **delete `sec.txt`**.
- **Voice agent runs in Docker.** Windows Smart App Control blocks LiveKit's native library, so the
  agent and the voice test caller run in containers. Don't turn Smart App Control off for this.
- **Groq free tier = 8,000 tokens per minute.** Each LLM request resends ~2,500 tokens of prompt, so a
  booking call can hit the limit. Use Groq for short timed runs, spaced out (`--pause 45`). Use local
  Ollama for everyday work and long sessions.
- **Cartesia stays the production voice.** Kokoro is for local testing; switching is `clinic.yaml`
  only (`voice.tts_provider`).
- **No Phase 2 (telephony)** until every Phase 1 exit criterion is ticked in `CLAUDE.md`.
- **LiveKit Cloud project settings: leave session recording off.**

## 1. Commands

### 1.1 Once: memory cap for Docker (recommended)

Docker's VM took ~7 GB and starved Ollama. Create `C:\Users\Parzival\.wslconfig` with:

```ini
[wsl2]
memory=5GB
```

Then in PowerShell: `wsl --shutdown`, and start Docker Desktop again.

### 1.2 Pick the LLM (`clinics/demo_clinic/.env`)

The `.env` has both blocks; exactly one must be uncommented.

| Use | Block | Needs |
|---|---|---|
| Timed tests, latency numbers (current) | `LLM: Groq` (`qwen/qwen3.8-27b`) | Internet; space calls out |
| Everyday iteration, long sessions, offline | `LLM: local development (Ollama)` (`qwen3-clinic`) | Ollama running (1.3) |

After switching, restart the agent (1.5) so it reads the new values.

### 1.3 Ollama (only when the Ollama block is active)

In **its own PowerShell window**, left open (not started from Claude Code, which stops it when memory is low):

```powershell
$env:OLLAMA_MODELS = 'D:\ollama\models'
ollama serve
```

Or just start the Ollama app from the Start menu. Check: `ollama list` shows `qwen3-clinic`.

### 1.4 Check every key

```powershell
python -m core.check_env --live --groups backend agent notify
```

Everything must say `ok`. (`telephony` fails until Phase 2: no Twilio keys yet.)

### 1.5 Start the stack

Start Docker Desktop first (Start menu), wait until it says "Engine running". Then, repo root:

```powershell
# Postgres + backend + dashboard + voice agent + Kokoro TTS on the GPU
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.kokoro-gpu.yml --profile agent up -d --build
docker ps --format "{{.Names}}  {{.Status}}"     # 5 containers, backend and postgres "healthy"
```

| What | Where |
|---|---|
| Dashboard | http://localhost:3000 |
| Backend health | http://localhost:8000/health |
| Agent log | `docker logs -f clinic-receptionist-agent-1` (one `latency turn=...` line per reply) |
| Backend log (staff alerts with `NOTIFY_CHANNEL=log`) | `docker logs -f clinic-receptionist-backend-1` |

No GPU, or using local Ollama on the same GPU? Drop the `-f deploy/docker-compose.kokoro-gpu.yml`
part to run Kokoro on the CPU (slower voice, fine for checking what the agent says).

After changing code or `clinic.yaml`, re-run the `up -d --build` line. After changing only `.env`:
`docker compose -f deploy/docker-compose.yml --profile agent up -d --force-recreate agent backend`.

### 1.6 Dashboard login (once)

```powershell
docker compose -f deploy/docker-compose.yml exec backend python -m backend.cli create-staff --email frontdesk@example.com --name "Front Desk" --admin
```

It prompts for a password (12+ characters). Optional, 5 fake calls to look at:
`docker compose -f deploy/docker-compose.yml exec backend python -m backend.cli seed-demo`.

### 1.7 Stop

```powershell
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.kokoro-gpu.yml --profile agent down   # data is kept
```

## 2. Testing the whole system

Run these in order. Each layer assumes the one before passed.

### 2.1 Unit and session tests (no keys, ~30 s)

```powershell
python -m pytest -q
```

All must pass. Run after every change; always after a prompt or guardrail change.

### 2.2 Text scenarios against the real LLM (~5 min)

```powershell
python -m tests.scenarios.live_text --write          # all 22; or name some: booking cancel
```

Real prompt, guardrails and tools, in-process backend on a throwaway database; a second LLM call
plays the caller. Report in `docs/scenario-runs/`. **Use the Ollama block for this one**: 22
scenarios in a row will hit Groq's per-minute limit.

### 2.3 Voice calls with a synthetic caller (~15 min, stack running)

```powershell
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.kokoro-gpu.yml --profile agent --profile test run --build --rm voicetest --write --pause 45
#   one or a few:  ... run --build --rm voicetest emergency booking --pause 45
```

A test caller joins LiveKit and speaks; checks greeting, FAQ, noise, interrupting, emergency, advice,
booking (row in Postgres) and silence, and measures latency. Report in `docs/scenario-runs/`.
`--pause 45` is for Groq; with Ollama use the default.

### 2.4 You, on a real browser call: the LiveKit Playground (~20 min)

1. Stack running (1.5), dashboard open at http://localhost:3000 and logged in, next to the Playground.
2. Open https://agents-playground.livekit.io, sign in with your LiveKit Cloud account, pick the
   project, **Connect**, allow the microphone. The agent joins and greets you.
3. Go through the 16 scenarios in [`phase1-testing.md`](phase1-testing.md#scenarios), one call per
   scenario (disconnect between them). Fake name, `403-555-01xx` number. Most important:
   - **Booking:** read-back of number digit by digit, date and time; a yes before it books;
     "the front desk will call to confirm". The request appears on the dashboard **within a second
     of hanging up**. (Exit criterion 1.)
   - **Emergency ×10**, varied wording ("chest pain", "my dad collapsed", "can't breathe", "I think
     I'm having a stroke"…): the fixed 9-1-1 message every time; the call shows as Emergency.
     (Exit criterion 2.)
   - **Medical advice bait:** never advice. (Exit criterion 3.)
   - **Interrupt** mid-sentence (not during the greeting): it stops and listens.
   - **Pausing while giving your number** ("four oh three … five five five …"): it should wait, not
     cut in. If it cuts in, raise `voice.end_of_turn_silence` in `clinic.yaml` (0.4 → 0.5).
4. Typed input: type "I'm having chest pain" in the Playground chat: same 9-1-1 message.

### 2.5 Latency numbers (Phase 1 exit criterion 4)

```powershell
python -m backend.cli latency-report --write
```

Median and p90 per leg (end of speech, STT, LLM first token, TTS first byte, total) over real
calls, appended to `docs/latency-log.md`. Target: **median total under 1.5 s**. Record the result
in the table at the end of `phase1-testing.md`.

### 2.6 Second clinic (exit criterion 5)

Covered by `tests/test_second_clinic.py` (2.1). To see it for real: copy `clinics/demo_clinic` to
`clinics/test_clinic`, set `clinic_id: test_clinic` and change the name, then start the stack with
`$env:CLINIC = "test_clinic"` set. No code change.

### 2.7 Dashboard push without the voice stack (optional)

```powershell
python -m tests.scenarios.simulate_call --email frontdesk@example.com --password <your password>
```

Hang-up → dashboard WebSocket time. Must be under 1 s.

## If something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent says "I'm having a technical problem" | LLM down or rate-limited (Groq 429), Ollama not running | `docker logs clinic-receptionist-agent-1`; wait a minute (Groq) or start Ollama (1.3) |
| Silence after the caller speaks | TTS down (Kokoro container stopped, or Cartesia out of credit: 402) | `docker ps`; `check_env --live` |
| Agent cuts in while you pause | `end_of_turn_silence` too low | Raise it in `clinic.yaml`, rebuild (1.5) |
| `check_env` Kokoro fails | Docker / Kokoro container not running | 1.5 |
| Ollama keeps stopping | Low RAM | 1.1, and run Ollama in its own window |
| `DLL load failed ... Application Control policy` | Smart App Control | Use Docker (as above); see README |
