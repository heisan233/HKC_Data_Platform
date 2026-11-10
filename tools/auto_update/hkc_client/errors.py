"""Errors shared by the one-shot collector client."""

from __future__ import annotations


class CollectorError(Exception):
    """A dataset or API error that should be reported without a traceback."""


class FatalCollectorError(CollectorError):
    """An error that makes the remaining queue unsafe to process."""


class TransientCollectorError(CollectorError):
    """A connection or server error that may succeed on retry."""
