"""Activity Chronicler — long-term desktop-activity memory built on OpenChronicle.

Turns the daily `event-YYYY-MM-DD.md` files (with their time-ranged sub_tasks)
into weekly / monthly narrative reports: time distribution, themes, observed
regularities, and week-over-week change. Time math is deterministic; LLM is
only used for clustering and narrative synthesis.
"""

from .stats import (
    SubTask,
    ActivityStats,
    parse_event_entries,
    compute_stats,
)
from .synthesizer import OpenThread, synthesize_recap
from .chronicler import build_recap, render_markdown

__all__ = [
    "SubTask",
    "ActivityStats",
    "parse_event_entries",
    "compute_stats",
    "synthesize_recap",
    "OpenThread",
    "build_recap",
    "render_markdown",
]
