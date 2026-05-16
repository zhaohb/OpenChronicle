# email_task_planner

Extracts **email-related tasks and due times** from OpenChronicle `event-*.md` and writes Markdown plus an optional **`.ics`** file for calendar import.

Uses only what was captured into memory (no IMAP / Gmail API).

## Setup

From `example/`: install the package and configure `.env` (`OC_MCP_URL`, `OC_LLM_*`). Start OpenChronicle daemon.

## Usage

```bash
oc-mailtasks
# or
python -m email_task_planner.planner

oc-mailtasks --yesterday
oc-mailtasks --date 2026-05-09
oc-mailtasks --since 2026-05-01 --until 2026-05-09

oc-mailtasks -o ./mailtasks/out.md --calendar-out ./mailtasks/out.ics
oc-mailtasks --remind-before 120 --default-due-time 09:00
oc-mailtasks --model qwen2.5:14b --mcp-url http://127.0.0.1:8742/mcp --verbose
```
