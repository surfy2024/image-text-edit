import json

import pytest

from edit_chart_text.request_io import load_request


def write_request(tmp_path, payload):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return request_path


def test_load_request_with_dynamic_replacements(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {"old_text": " P10 ", "new_text": " P40 ", "scope": "one", "location_hint": "左上"},
                {"old_text": "25.00", "new_text": "26.50"},
            ],
        },
    )

    request = load_request(request_path)

    assert request.image_path == tmp_path / "chart.png"
    assert len(request.replacements) == 2
    assert request.replacements[0].old_text == "P10"
    assert request.replacements[0].new_text == "P40"
    assert request.replacements[0].scope == "one"
    assert request.replacements[0].location_hint == "左上"
    assert request.replacements[1].old_text == "25.00"
    assert request.replacements[1].new_text == "26.50"
    assert request.replacements[1].scope == "ask"
    assert request.replacements[0].match_mode == "exact"
    assert request.replacements[0].substring_occurrence is None


def test_load_request_accepts_explicit_substring_mode(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "25.00",
                    "new_text": "26.50",
                    "match_mode": "substring",
                }
            ],
        },
    )

    request = load_request(request_path)

    assert request.replacements[0].match_mode == "substring"
    assert request.replacements[0].substring_occurrence is None


def test_load_request_accepts_confirmed_substring_occurrence(tmp_path) -> None:
    polygon = [[10, 10], [30, 10], [30, 24], [10, 24]]
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "confirmation_report_path": "prior.json",
            "replacements": [
                {
                    "old_text": "25",
                    "new_text": "26",
                    "match_mode": "substring",
                    "substring_occurrence": 2,
                    "scope": "one",
                    "candidate_number": 1,
                    "candidate_polygon": polygon,
                    "candidate_token": "secure-token-value",
                }
            ],
        },
    )

    request = load_request(request_path)

    assert request.replacements[0].match_mode == "substring"
    assert request.replacements[0].substring_occurrence == 2


def test_load_request_rejects_unknown_match_mode_with_context(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {"old_text": "P10", "new_text": "P40", "match_mode": "fuzzy"}
            ],
        },
    )

    with pytest.raises(ValueError, match=r"replacements\[0\]\.match_mode"):
        load_request(request_path)


def test_load_request_rejects_equal_text_in_substring_mode(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {"old_text": "P10", "new_text": "P10", "match_mode": "substring"}
            ],
        },
    )

    with pytest.raises(ValueError, match=r"replacements\[0\].*old_text.*new_text"):
        load_request(request_path)


def test_load_request_rejects_occurrence_in_exact_mode(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "P10",
                    "new_text": "P40",
                    "match_mode": "exact",
                    "substring_occurrence": 1,
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"replacements\[0\]\.substring_occurrence"):
        load_request(request_path)


@pytest.mark.parametrize("occurrence", [0, True])
def test_load_request_rejects_invalid_substring_occurrence(
    tmp_path, occurrence
) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "P10",
                    "new_text": "P40",
                    "match_mode": "substring",
                    "substring_occurrence": occurrence,
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"replacements\[0\]\.substring_occurrence"):
        load_request(request_path)


def test_load_request_rejects_occurrence_without_confirmation_report_with_context(
    tmp_path,
) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "P10",
                    "new_text": "P40",
                    "match_mode": "substring",
                    "substring_occurrence": 1,
                    "scope": "one",
                    "candidate_number": 1,
                    "candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]],
                    "candidate_token": "secure-token-value",
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match=r"replacements\[0\].*confirmation_report_path",
    ):
        load_request(request_path)


def test_load_request_rejects_occurrence_without_candidate_selection(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "P10",
                    "new_text": "P40",
                    "match_mode": "substring",
                    "substring_occurrence": 1,
                    "scope": "one",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"replacements\[0\].*candidate selection"):
        load_request(request_path)


@pytest.mark.parametrize(
    "replacement",
    [
        {"new_text": "P40"},
        {"old_text": "P10"},
        {"old_text": "   ", "new_text": "P40"},
        {"old_text": "P10", "new_text": "   "},
    ],
)
def test_load_request_rejects_missing_or_empty_text(tmp_path, replacement) -> None:
    request_path = write_request(
        tmp_path, {"image_path": "chart.png", "replacements": [replacement]}
    )

    with pytest.raises(ValueError, match="old_text and new_text"):
        load_request(request_path)


def test_load_request_rejects_invalid_scope(tmp_path) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {"old_text": "P10", "new_text": "P40", "scope": "some"}
            ],
        },
    )

    with pytest.raises(ValueError, match="scope"):
        load_request(request_path)


def test_load_request_rejects_empty_replacement_list(tmp_path) -> None:
    request_path = write_request(
        tmp_path, {"image_path": "chart.png", "replacements": []}
    )

    with pytest.raises(ValueError, match="replacement"):
        load_request(request_path)

@pytest.mark.parametrize("payload", [None, [], "request", 42, True])
def test_load_request_requires_top_level_object(tmp_path, payload) -> None:
    request_path = write_request(tmp_path, payload)
    with pytest.raises(ValueError, match="top-level"):
        load_request(request_path)


@pytest.mark.parametrize("image_path", [None, "", "   ", 42, True, [], {}])
def test_load_request_requires_non_empty_string_image_path(tmp_path, image_path) -> None:
    request_path = write_request(tmp_path, {"image_path": image_path, "replacements": [{"old_text": "P10", "new_text": "P40"}]})
    with pytest.raises(ValueError, match="image_path"):
        load_request(request_path)


def test_load_request_requires_image_path(tmp_path) -> None:
    request_path = write_request(tmp_path, {"replacements": [{"old_text": "P10", "new_text": "P40"}]})
    with pytest.raises(ValueError, match="image_path"):
        load_request(request_path)


@pytest.mark.parametrize("replacements", [None, {}, "items", 42, True])
def test_load_request_requires_replacements_list(tmp_path, replacements) -> None:
    request_path = write_request(tmp_path, {"image_path": "chart.png", "replacements": replacements})
    with pytest.raises(ValueError, match="replacements"):
        load_request(request_path)


@pytest.mark.parametrize("item", [None, [], "item", 42, True])
def test_load_request_requires_replacement_objects(tmp_path, item) -> None:
    request_path = write_request(tmp_path, {"image_path": "chart.png", "replacements": [item]})
    with pytest.raises(ValueError, match=r"replacements\[0\]"):
        load_request(request_path)


@pytest.mark.parametrize("field", ["old_text", "new_text"])
@pytest.mark.parametrize("value", [None, 42, True, [], {}])
def test_load_request_requires_replacement_text_strings(tmp_path, field, value) -> None:
    replacement = {"old_text": "P10", "new_text": "P40"}
    replacement[field] = value
    request_path = write_request(tmp_path, {"image_path": "chart.png", "replacements": [replacement]})
    with pytest.raises(ValueError, match=field):
        load_request(request_path)


@pytest.mark.parametrize("location_hint", ["", "   ", 42, True, [], {}])
def test_load_request_rejects_invalid_location_hint(tmp_path, location_hint) -> None:
    request_path = write_request(tmp_path, {"image_path": "chart.png", "replacements": [{"old_text": "P10", "new_text": "P40", "location_hint": location_hint}]})
    with pytest.raises(ValueError, match="location_hint"):
        load_request(request_path)


@pytest.mark.parametrize("scope", [None, 42, True, [], {}])
def test_load_request_rejects_non_string_scope(tmp_path, scope) -> None:
    request_path = write_request(tmp_path, {"image_path": "chart.png", "replacements": [{"old_text": "P10", "new_text": "P40", "scope": scope}]})
    with pytest.raises(ValueError, match="scope"):
        load_request(request_path)


def test_load_request_preserves_candidate_fingerprint_and_defaults_to_none(tmp_path) -> None:
    polygon = [[40, 10], [60, 10], [60, 24], [40, 24]]
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "confirmation_report_path": "prior.json",
            "replacements": [
                {
                    "old_text": "HZ",
                    "new_text": "CS",
                    "scope": "one",
                    "candidate_number": 2,
                    "candidate_polygon": polygon,
                    "candidate_token": "secure-token-value",
                },
                {"old_text": "P10", "new_text": "P20"},
            ],
        },
    )

    request = load_request(request_path)

    assert request.replacements[0].candidate_number == 2
    assert request.replacements[0].candidate_polygon == tuple(
        tuple(point) for point in polygon
    )
    assert request.replacements[0].candidate_token == "secure-token-value"
    assert request.replacements[1].candidate_number is None
    assert request.replacements[1].candidate_polygon is None


@pytest.mark.parametrize("candidate_number", [None, True, False, 0, -1, 1.5, "2"])
def test_load_request_rejects_invalid_candidate_number(
    tmp_path, candidate_number
) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "HZ",
                    "new_text": "CS",
                    "scope": "one",
                    "candidate_number": candidate_number,
                    "candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="candidate_number"):
        load_request(request_path)


@pytest.mark.parametrize(
    "replacement",
    [
        {"candidate_number": 1},
        {"candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]]},
    ],
)
def test_load_request_requires_candidate_number_and_polygon_together(
    tmp_path, replacement
) -> None:
    replacement = {"old_text": "HZ", "new_text": "CS", "scope": "one", **replacement}
    request_path = write_request(
        tmp_path, {"image_path": "chart.png", "replacements": [replacement]}
    )

    with pytest.raises(ValueError, match="together"):
        load_request(request_path)


@pytest.mark.parametrize("scope", ["all", "ask"])
def test_load_request_rejects_selection_outside_scope_one(tmp_path, scope) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "HZ",
                    "new_text": "CS",
                    "scope": scope,
                    "candidate_number": 1,
                    "candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="scope=one"):
        load_request(request_path)


@pytest.mark.parametrize(
    "polygon",
    [
        None,
        True,
        [],
        [[0, 0], [1, 0], [1, 1]],
        [[0, 0], [1, 0], [1, 1], [False, 1]],
        [[0, 0], [1.5, 0], [1, 1], [0, 1]],
        [[0, 0], [1, 0], [1, "1"], [0, 1]],
        [[0, 0], [1, 0], [2, 0], [3, 0]],
        [[0, 0], [1, 1], [0, 1], [1, 0]],
    ],
)
def test_load_request_rejects_invalid_candidate_polygon(tmp_path, polygon) -> None:
    request_path = write_request(
        tmp_path,
        {
            "image_path": "chart.png",
            "replacements": [
                {
                    "old_text": "HZ",
                    "new_text": "CS",
                    "scope": "one",
                    "candidate_number": 1,
                    "candidate_polygon": polygon,
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="candidate_polygon"):
        load_request(request_path)


def test_load_request_requires_report_token_number_and_polygon_as_one_selection(tmp_path):
    base = {
        "old_text": "HZ", "new_text": "CS", "scope": "one",
        "candidate_number": 1,
        "candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]],
    }
    for missing in ("confirmation_report_path", "candidate_token"):
        payload = {"image_path": "chart.png", "confirmation_report_path": "prior.json",
                   "replacements": [{**base, "candidate_token": "secure-token-value"}]}
        if missing == "confirmation_report_path":
            payload.pop(missing)
        else:
            payload["replacements"][0].pop(missing)
        path = write_request(tmp_path, payload)
        with pytest.raises(ValueError, match=missing):
            load_request(path)


def test_load_request_preserves_confirmation_report_and_token(tmp_path):
    path = write_request(tmp_path, {
        "image_path": "chart.png",
        "confirmation_report_path": "chart_run_edit-report.json",
        "replacements": [{
            "old_text": "HZ", "new_text": "CS", "scope": "one",
            "candidate_number": 1, "candidate_token": "secure-token-value",
            "candidate_polygon": [[10, 10], [30, 10], [30, 24], [10, 24]],
        }],
    })
    request = load_request(path)
    assert request.confirmation_report_path == (tmp_path / "chart_run_edit-report.json").resolve()
    assert request.replacements[0].candidate_token == "secure-token-value"
