"""Meeting and task digest — turns OpenChronicle event-daily entries into structured reports."""

from .extractor import DigestResult, MeetingDigest, extract_digest

__all__ = ["DigestResult", "MeetingDigest", "extract_digest"]
