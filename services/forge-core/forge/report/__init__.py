"""Client-facing output: the control review and its evidence bundle."""

from .diagnostic import render_diagnostic, summary_sentence, write_diagnostic, write_evidence_bundle

__all__ = ["render_diagnostic", "write_diagnostic", "write_evidence_bundle", "summary_sentence"]
