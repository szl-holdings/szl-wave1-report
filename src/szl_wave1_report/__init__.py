"""SZL Wave 1 report: verifiable bakeoff consolidation."""

from .report import GENESIS, LaneResult, aggregate, canonical, render_markdown, verify_chain

__all__ = ["GENESIS", "LaneResult", "aggregate", "canonical", "render_markdown", "verify_chain"]
__version__ = "0.1.0"
