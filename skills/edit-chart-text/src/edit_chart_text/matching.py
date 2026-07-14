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


def substring_occurrences(text: str, needle: str) -> tuple[int, ...]:
    """Return zero-based positions of non-overlapping literal occurrences."""
    if not needle:
        raise ValueError("needle must not be empty")

    positions: list[int] = []
    start = 0
    while (position := text.find(needle, start)) != -1:
        positions.append(position)
        start = position + len(needle)
    return tuple(positions)


def derive_target_label(
    replacement: Replacement,
    candidate: TextCandidate,
    occurrence: int | None = None,
) -> tuple[str, str, int | None]:
    """Return the complete source and target labels for one candidate."""
    if replacement.match_mode == "exact":
        return candidate.text, replacement.new_text, None

    positions = substring_occurrences(candidate.text, replacement.old_text)
    selected = occurrence
    if selected is None:
        selected = replacement.substring_occurrence
    if selected is None:
        if len(positions) != 1:
            raise ValueError("substring occurrence must be specified unless unique")
        selected = 1
    if type(selected) is not int or selected <= 0:
        raise ValueError("substring occurrence must be a positive integer")
    if selected > len(positions):
        raise ValueError("substring occurrence is out of range")

    position = positions[selected - 1]
    target = (
        candidate.text[:position]
        + replacement.new_text
        + candidate.text[position + len(replacement.old_text) :]
    )
    return candidate.text, target, selected


def choose_candidates(
    replacement: Replacement, candidates: tuple[TextCandidate, ...]
) -> MatchDecision:
    """Return usable candidates without silently guessing ambiguous or fuzzy text."""
    if replacement.match_mode == "substring":
        literal = tuple(
            item for item in candidates
            if item.confidence >= 0.80 and replacement.old_text in item.text
        )
        if not literal:
            return MatchDecision("not_found")
        if any(
            len(substring_occurrences(item.text, replacement.old_text)) != 1
            for item in literal
        ):
            return MatchDecision("needs_confirmation", literal)
        if replacement.scope == "all" or len(literal) == 1:
            return MatchDecision("ready", literal)
        return MatchDecision("needs_confirmation", literal)

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
