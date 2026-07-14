import pytest

from edit_chart_text.matching import (
    choose_candidates,
    derive_target_label,
    substring_occurrences,
)
from edit_chart_text.models import Replacement, TextCandidate


def candidate(text: str, confidence: float = 0.9, x: int = 0) -> TextCandidate:
    return TextCandidate(text, ((x, 0), (x + 10, 0), (x + 10, 10), (x, 10)), confidence)


def test_all_returns_every_exact_match_in_input_order() -> None:
    choices = (candidate("A", x=20), candidate("B"), candidate("A", x=0))
    result = choose_candidates(Replacement("A", "Z", "all"), choices)
    assert result.status == "ready"
    assert result.candidates == (choices[0], choices[2])


def test_unique_exact_is_ready_for_ask() -> None:
    choice = candidate("A")
    assert choose_candidates(Replacement("A", "Z"), (choice,)).candidates == (choice,)
    assert choose_candidates(Replacement("A", "Z"), (choice,)).status == "ready"


def test_multiple_ask_and_one_need_confirmation() -> None:
    choices = (candidate("A"), candidate("A", x=20))
    assert choose_candidates(Replacement("A", "Z", "ask"), choices).status == "needs_confirmation"
    assert choose_candidates(Replacement("A", "Z", "one"), choices).status == "needs_confirmation"


def test_fuzzy_matches_never_auto_ready_and_preserve_order() -> None:
    choices = (candidate("P1O"), candidate("P10", confidence=0.79), candidate("P100"))
    result = choose_candidates(Replacement("P10", "P40", "all"), choices)
    assert result.status == "needs_confirmation"
    assert result.candidates == choices


def test_not_found_and_case_sensitive_matching() -> None:
    result = choose_candidates(Replacement("Label", "X"), (candidate("LABEL"), candidate("other")))
    assert result.status == "not_found"
    assert result.candidates == ()


def test_low_confidence_exact_is_excluded_from_ready_but_remains_fuzzy_confirmation() -> None:
    choice = candidate("A", 0.79)
    result = choose_candidates(Replacement("A", "Z"), (choice,))
    assert result.status == "needs_confirmation"
    assert result.candidates == (choice,)


def test_substring_all_returns_unique_literal_matches_in_input_order() -> None:
    choices = (
        candidate("HZ21-1A/B平台", x=20),
        candidate("other"),
        candidate("HZ25-4DPP"),
    )
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")

    result = choose_candidates(replacement, choices)

    assert result.status == "ready"
    assert result.candidates == (choices[0], choices[2])
    assert derive_target_label(replacement, choices[0]) == (
        "HZ21-1A/B平台",
        "CS21-1A/B平台",
        1,
    )
    assert derive_target_label(replacement, choices[2]) == (
        "HZ25-4DPP",
        "CS25-4DPP",
        1,
    )


def test_substring_matching_is_case_sensitive_and_never_falls_back_to_fuzzy() -> None:
    choices = (candidate("hz21-1A"), candidate("H2", x=20))
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")

    result = choose_candidates(replacement, choices)

    assert result.status == "not_found"
    assert result.candidates == ()


def test_repeated_substring_requires_confirmation_and_can_resolve_each_occurrence() -> None:
    choice = candidate("HZ-HZ")
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")

    assert substring_occurrences(choice.text, replacement.old_text) == (0, 3)
    assert choose_candidates(replacement, (choice,)).status == "needs_confirmation"
    assert derive_target_label(replacement, choice, 1) == ("HZ-HZ", "CS-HZ", 1)
    assert derive_target_label(replacement, choice, 2) == ("HZ-HZ", "HZ-CS", 2)
    with pytest.raises(ValueError):
        derive_target_label(replacement, choice, 3)


def test_repeated_substring_without_occurrence_is_rejected() -> None:
    replacement = Replacement("HZ", "CS", match_mode="substring")

    with pytest.raises(ValueError):
        derive_target_label(replacement, candidate("HZ-HZ"))


def test_replacement_substring_occurrence_is_used_by_default() -> None:
    replacement = Replacement(
        "HZ", "CS", match_mode="substring", substring_occurrence=2
    )

    assert derive_target_label(replacement, candidate("HZ-HZ")) == (
        "HZ-HZ",
        "HZ-CS",
        2,
    )


def test_exact_target_label_does_not_derive_a_substring() -> None:
    replacement = Replacement("HZ", "CS")

    assert derive_target_label(replacement, candidate("HZ")) == ("HZ", "CS", None)


def test_low_confidence_literal_is_not_a_substring_candidate() -> None:
    choice = candidate("HZ21-1A", confidence=0.79)
    replacement = Replacement("HZ", "CS", "all", match_mode="substring")

    result = choose_candidates(replacement, (choice,))

    assert result.status == "not_found"
    assert result.candidates == ()
