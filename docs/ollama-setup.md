# Ollama + Qwen Setup (Windows)

Local LLM for the AI Clinic Voice Receptionist. Free, unlimited, offline, OpenAI-compatible.

**Read this first:** Ollama's OpenAI-compatible endpoint defaults to a **2,048-token context window** and gives you no way to raise it from the API. Our voice agent sends a system prompt, five tool definitions and a growing conversation history on every turn — that blows past 2,048 within two or three exchanges, and the symptom is the agent silently "forgetting" the start of the call or dropping its tools. **Step 4 fixes this and is not optional.**

---

## Before you start — check your machine

Open PowerShell (Win + X → Terminal) and run:

```powershell
# RAM, in bytes
(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB

# GPU
Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM
```

Pick your model from the result:

| Your situation | Model | Download | Notes |
| --- | --- | --- | --- |
| 16 GB+ RAM, or any NVIDIA GPU with 6 GB+ VRAM | **`qwen2.5:7b`** | 4.7 GB | The target. Use this. |
| 8 GB RAM, no GPU | `qwen2.5:3b` | 1.9 GB | Works, weaker at tool calling. Expect to re-test booking flows. |
| 32 GB+ RAM with a 12 GB+ GPU | `qwen2.5:14b` | 9.0 GB | Better quality, slower. Only if the 7B disappoints. |

**Why Qwen 2.5 and not Qwen 3:** Qwen 3 is a *thinking* model — it generates reasoning tokens before answering. That is excellent for hard problems and wrong for a phone call, where every extra token is dead air on the line. Qwen 2.5 answers directly. Stick with 2.5 for this project.

An NVIDIA GPU makes this roughly 5–10× faster than CPU. Without one it still works, just expect 3–8 seconds per reply locally — fine for building and testing logic, which is what local inference is *for*. Timed latency runs happen against Groq.

---

## Step 1 — Install Ollama

Download and run the installer:

**https://ollama.com/download/OllamaSetup.exe**

Or, in PowerShell:

```powershell
irm https://ollama.com/install.ps1 | iex
```

The installer needs no admin rights and installs to your user profile. When it finishes, Ollama runs as a background service with a tray icon, listening on `http://127.0.0.1:11434`.

Verify:

```powershell
ollama --version
```

If `ollama` is not recognised, close and reopen PowerShell — the installer adds it to PATH and open terminals don't pick that up.

---

## Step 2 — Move model storage off C: *(recommended)*

By default models land in `C:\Users\<you>\.ollama\models`. Three or four models will eat 20+ GB of your system drive. Since your project lives on D:, put the models there too.

1. **Quit Ollama** — right-click the tray icon → Quit. (Not just close the window; the service must stop.)
2. Press Win, type `environment variables`, open **Edit environment variables for your account**.
3. Under *User variables*, click **New**:
   - Variable name: `OLLAMA_MODELS`
   - Variable value: `D:\ollama\models`
4. Click **OK**, then **OK** again.
5. Create the folder: `mkdir D:\ollama\models`
6. Start Ollama again from the Start menu.

Do this **before** pulling models. If you've already pulled some, they stay in the old location — move the contents of `C:\Users\<you>\.ollama\models` into `D:\ollama\models` manually, or just re-pull.

---

## Step 3 — Pull Qwen

```powershell
ollama pull qwen2.5:7b
```

4.7 GB. Grab a coffee.

Test it:

```powershell
ollama run qwen2.5:7b "Say hello in one short sentence."
```

You should get a reply in a few seconds. Type `/bye` to exit if you land in the interactive prompt.

---

## Step 4 — Fix the context window ⚠️

**This is the step everyone skips and then spends a day debugging.**

Ollama defaults to a 2,048-token context. The OpenAI-compatible API provides no parameter to change it — the only way is to bake it into a model variant.

Create a file called `Modelfile` (no extension) in your project folder:

```
FROM qwen2.5:7b

PARAMETER num_ctx 8192
PARAMETER temperature 0.4
PARAMETER top_p 0.9
```

Then build the variant:

```powershell
cd "D:\projects\ranga sir\agentic assistance"
ollama create qwen-clinic -f Modelfile
```

```powershell
ollama list
```

You should now see `qwen-clinic:latest` alongside `qwen2.5:7b`. **Use `qwen-clinic` as the model name everywhere in the project.**

**What the parameters do:**

- `num_ctx 8192` — room for the system prompt (~800 tokens), five tool definitions (~600 tokens) and a full three-minute conversation, with headroom. Raise to 16384 if you add long FAQ content; each doubling costs roughly 1 GB of RAM.
- `temperature 0.4` — low. A receptionist should be consistent and boring, not creative. Higher values invent clinic policies.
- `top_p 0.9` — standard.

Re-run `ollama create` any time you change the Modelfile. It's instant — it reuses the already-downloaded weights.

---

## Step 5 — Verify the OpenAI-compatible endpoint

This is the interface the LiveKit agent actually uses, so test it directly rather than trusting `ollama run`.

```powershell
curl http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"qwen-clinic\",\"messages\":[{\"role\":\"user\",\"content\":\"Say this is a test\"}]}"
```

You should get back JSON in OpenAI's shape, with `choices[0].message.content`.

In Python:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # required by the client, ignored by Ollama
)

r = client.chat.completions.create(
    model="qwen-clinic",
    messages=[{"role": "user", "content": "Say this is a test"}],
)
print(r.choices[0].message.content)
```

---

## Step 6 — Verify tool calling ⚠️

The entire booking flow depends on this. Test it before building anything on top.

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

tools = [{
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": "Check open appointment slots for a given date and service.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "service": {"type": "string"},
            },
            "required": ["date", "service"],
        },
    },
}]

r = client.chat.completions.create(
    model="qwen-clinic",
    messages=[
        {"role": "system", "content": "You are a clinic receptionist. Today is 2026-11-09."},
        {"role": "user", "content": "Do you have anything open for a cleaning next Tuesday?"},
    ],
    tools=tools,
)

print(r.choices[0].message.tool_calls)
```

**Expected:** a tool call to `check_availability` with a `date` and `service`. If you get `None` and a chatty text reply instead, the model isn't invoking tools — check that you used `qwen-clinic` (not a base model), that `tools` is being passed, and that the system prompt tells it today's date.

**Known limitation:** Ollama's OpenAI endpoint does **not** support `tool_choice`. You cannot force a specific tool. The agent code must handle the model choosing to reply in text when you expected a tool call — which is exactly the retry-then-fall-back-to-`take_message` behaviour in section 7 of `CLAUDE.md`.

---

## Step 7 — Wire it into the project

In `clinics/demo_clinic/.env`:

```env
# Local development (Ollama)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen-clinic

# Timed test passes (Groq) — swap these in, change nothing else
# LLM_BASE_URL=https://api.groq.com/openai/v1
# LLM_API_KEY=gsk_your_key_here
# LLM_MODEL=qwen/qwen3-32b
```

Switching between local and Groq must be *only* these three lines. If it ever requires a code change, `core/providers/llm.py` is wrong.

---

## Step 8 — Keep it responsive

By default Ollama unloads a model from memory after 5 minutes idle, so the first call after a break pays a 10–30 second reload. During a working session, keep it loaded:

1. Quit Ollama from the tray.
2. Add another user environment variable (same route as Step 2):
   - `OLLAMA_KEEP_ALIVE` = `2h`
3. Restart Ollama.

Use `-1` to keep it loaded indefinitely, or `0` to unload immediately after each request.

Other variables worth knowing:

| Variable | Default | What it does |
| --- | --- | --- |
| `OLLAMA_KEEP_ALIVE` | `5m` | How long a model stays in memory |
| `OLLAMA_MODELS` | `C:\Users\<you>\.ollama\models` | Where weights are stored |
| `OLLAMA_HOST` | `127.0.0.1:11434` | Bind address — set to `0.0.0.0` only if something outside your machine must reach it |
| `OLLAMA_NUM_PARALLEL` | auto (1 or 4) | Concurrent requests. **Careful:** parallel slots divide the context — 4 parallel with `num_ctx 8192` gives each request 2,048 again. Set to `1` while developing. |

That last row is the sibling of the Step 4 trap. If the agent starts truncating history after you've already set `num_ctx`, check this.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ollama: command not found` | PATH not refreshed | Close and reopen PowerShell |
| Connection refused on :11434 | Service not running | Start Ollama from the Start menu; check the tray icon |
| Agent forgets the start of the call | Context still 2,048 | Step 4 — confirm you're calling `qwen-clinic`, not `qwen2.5:7b` |
| Context shrinks despite Step 4 | Parallel slots splitting it | Set `OLLAMA_NUM_PARALLEL=1` |
| Replies take 15+ seconds | Running on CPU, or model too large | Check GPU is detected in the Ollama log; drop to `qwen2.5:3b` |
| Tool calls never fire | Model too small, or tools not passed | Confirm `tools=` in the request; try the 7B if you're on 3B |
| Out of memory on load | `num_ctx` too high for your RAM | Lower to 4096 |
| Disk filling on C: | `OLLAMA_MODELS` set after pulling | Move `C:\Users\<you>\.ollama\models` contents to `D:\ollama\models` |

To see what's loaded and how much memory it's using:

```powershell
ollama ps
```

---

## Quick reference

```powershell
ollama list                      # installed models
ollama ps                        # currently loaded
ollama pull qwen2.5:7b           # download
ollama create qwen-clinic -f Modelfile   # build the project variant
ollama run qwen-clinic           # interactive chat
ollama rm <model>                # delete
```

| | |
| --- | --- |
| Base URL | `http://localhost:11434/v1` |
| API key | `ollama` (any string — ignored) |
| Project model | `qwen-clinic` |
| Context | 8,192 tokens |
| Tools | Supported. `tool_choice` is not. |
| Streaming | Supported. |
