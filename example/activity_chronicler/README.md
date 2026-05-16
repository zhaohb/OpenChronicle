# activity_chronicler

Builds a **desktop activity recap** from OpenChronicle `event-*.md` (and light durable context). Output goes under `./recaps/` by default.

## Setup

From `example/`: `uv sync` or `pip install -e .`, copy `.env` from `env.example`. Run OpenChronicle daemon first.

## Usage

```bash
oc-recap
# or
python -m activity_chronicler.chronicler

# ISO week
oc-recap --week 2026-W19

# Calendar month
oc-recap --month 2026-05

# Date range
oc-recap --since 2026-05-14 --until 2026-05-17

# Options
oc-recap --no-compare-previous
oc-recap --model qwen2.5:14b --mcp-url http://127.0.0.1:8742/mcp -o ./recaps/out.md
oc-recap --previous-state ./recaps/state-prev.json --save-state ./recaps/state-this.json
oc-recap --owner "Me" --verbose
```

Default output path is `./recaps/activity-<window>.md` when `-o` is omitted.
