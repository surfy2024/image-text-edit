import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import TextCandidate


def _candidate(text):
    return TextCandidate(text, ((10, 10), (68, 10), (68, 24), (10, 24)), 0.95)


def _edit(*, match_mode="exact"):
    return {
        "old_text": "HZ",
        "new_text": "CS",
        "match_mode": match_mode,
        "source_label": "HZ26-6DPP（待建）",
        "target_label": "CS26-6DPP（待建）",
        "polygon": [[10, 10], [70, 10], [70, 24], [10, 24]],
        "allowed_box": [8, 8, 72, 26],
    }


def test_substring_post_ocr_accepts_ascii_parentheses_for_complete_chinese_label():
    passed, results, _ = pipeline._post_validate(
        (_candidate("CS26-6DPP(待建)"),), (_edit(match_mode="substring"),), (100, 40)
    )

    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["new_text_matches"] == 1


def test_exact_post_ocr_does_not_normalize_chinese_parentheses():
    passed, results, _ = pipeline._post_validate(
        (_candidate("CS26-6DPP(待建)"),), (_edit(),), (100, 40)
    )

    assert passed is False
    assert results[0]["new_text_matches"] == 0


def test_substring_post_ocr_rejects_wrong_complete_label_after_parenthesis_normalization():
    passed, results, _ = pipeline._post_validate(
        (_candidate("CS26-8DPP(待建)"),), (_edit(match_mode="substring"),), (100, 40)
    )

    assert passed is False
    assert results[0]["new_text_matches"] == 0


def test_substring_post_ocr_rejects_mixed_fullwidth_open_ascii_close():
    passed, results, _ = pipeline._post_validate(
        (_candidate("CS26-6DPP（待建)"),), (_edit(match_mode="substring"),), (100, 40)
    )

    assert passed is False
    assert results[0]["new_text_matches"] == 0


def test_substring_post_ocr_rejects_mixed_ascii_open_fullwidth_close():
    passed, results, _ = pipeline._post_validate(
        (_candidate("CS26-6DPP(待建）"),), (_edit(match_mode="substring"),), (100, 40)
    )

    assert passed is False
    assert results[0]["new_text_matches"] == 0

def test_substring_post_ocr_detects_old_label_with_ascii_parentheses():
    passed, results, messages = pipeline._post_validate(
        (_candidate("HZ26-6DPP(待建)"),), (_edit(match_mode="substring"),), (100, 40)
    )

    assert passed is False
    assert results[0]["old_text_matches"] == 1
    assert any("old_text" in message for message in messages)
