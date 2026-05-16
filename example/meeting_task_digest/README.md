# meeting_task_digest

Turns OpenChronicle **event-daily** entries into a **meetings and tasks** report (Markdown).

## Setup

From `example/`: install the package and configure `.env`. Start OpenChronicle daemon.

## Usage

```bash
oc-digest
# or
python -m meeting_task_digest.digest

oc-digest --yesterday
oc-digest --date 2026-05-08
oc-digest --since 2026-05-04 --until 2026-05-10 -o ./digests/week.md
oc-digest --model qwen2.5:14b --mcp-url http://127.0.0.1:8742/mcp --verbose
```
