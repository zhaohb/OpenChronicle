# OpenChronicle examples

Sample apps that **read** OpenChronicle local memory via MCP and produce Markdown (and optional calendar files). They do not write back to `~/.openchronicle/memory/`.

| App | CLI | What it does |
| --- | --- | --- |
| `meeting_task_digest` | `oc-digest` | Meetings and tasks from `event-*.md` |
| `handover_assistant` | `oc-handover` | Handover draft from projects + recent events |
| `activity_chronicler` | `oc-recap` | Weekly/monthly activity recap |
| `email_task_planner` | `oc-mailtasks` | Email-related tasks + optional `.ics` |

## Prereqs

1. **OpenChronicle** daemon running (MCP at `http://127.0.0.1:8742/mcp` by default).
2. **Local LLM** (e.g. Ollama) reachable; see `.env.example`.
3. **Install** this package from the `example/` directory.

## Setup

```bash
cd path/to/OpenChronicle/example
uv sync
# or: python -m pip install -e .
cp env.example .env   # Windows: copy env.example .env
```

Edit `.env` if needed (`OC_MCP_URL`, `OC_LLM_BASE_URL`, `OC_LLM_MODEL`).

## Usage (short)

```bash
# Today’s meeting + task digest
oc-digest

# Handover draft (default ~30 days)
oc-handover

# Current ISO week recap
oc-recap

# Email tasks + calendar for today
oc-mailtasks
```

Each subfolder has its own `README.md` with common flags (`--since`, `--until`, `--model`, `-o`, etc.).

## More detail

See `activity_chronicler/README.md`, `email_task_planner/README.md`, `meeting_task_digest/README.md`, `handover_assistant/README.md`.
