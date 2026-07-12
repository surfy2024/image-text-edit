from edit_chart_text.matching import choose_candidates
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
