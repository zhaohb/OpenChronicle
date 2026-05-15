"""Email task planner built on OpenChronicle's local memory layer.

The package extracts actionable email tasks from OpenChronicle event-daily
entries and exports both a Markdown report and an importable iCalendar file.
It does not connect to IMAP, Graph, Gmail, or any mailbox API.
"""

from .calendar_export import export_ics
from .extractor import EmailTask, EmailTaskResult, extract_email_tasks, render_markdown

__all__ = [
    "EmailTask",
    "EmailTaskResult",
    "extract_email_tasks",
    "export_ics",
    "render_markdown",
]
