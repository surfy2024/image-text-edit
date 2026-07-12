"""Deterministic selection of OCR candidates for requested replacements."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from .models import Replacement, TextCandidate


MatchStatus = Literal["ready", "needs_confirmation", "not_found"]


@dataclass(frozen=True)
class MatchDecision:
    status: MatchStatus
    candidates: tuple[TextCandidate, ...] = ()


def choose_candidates(
    replacement: Replacement, candidates: tuple[TextCandidate, ...]
) -> MatchDecision:
    """Return usable candidates without silently guessing ambiguous or fuzzy text."""
    exact = tuple(
        item for item in candidates
        if item.text == replacement.old_text and item.confidence >= 0.80
    )
    if exact:
        if replacement.scope == "all" or len(exact) == 1:
            return MatchDecision("ready", exact)
        return MatchDecision("needs_confirmation", exact)

    fuzzy = tuple(
        item for item in candidates
        if SequenceMatcher(None, replacement.old_text, item.text).ratio() >= 0.60
    )
    if fuzzy:
        return MatchDecision("needs_confirmation", fuzzy)
    return MatchDecision("not_found")
