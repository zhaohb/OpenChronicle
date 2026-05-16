# handover_assistant

Drafts a **handover document** from OpenChronicle project/person/org/tool memory plus recent `event-*` activity.

## Setup

From `example/`: install the package and configure `.env`. Start OpenChronicle daemon.

## Usage

```bash
oc-handover
# or
python -m handover_assistant.handover

oc-handover --since 2026-04-01 --until 2026-05-09
oc-handover --project openchronicle --owner "Jane Doe" -o ./handover/out.md
oc-handover --model qwen2.5:14b --mcp-url http://127.0.0.1:8742/mcp --verbose
```

Treat the file as a **first draft**; review before sharing.
