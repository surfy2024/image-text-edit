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

    assert request.image_path.name == "chart.png"
    assert len(request.replacements) == 2
    assert request.replacements[0].old_text == "P10"
    assert request.replacements[0].new_text == "P40"
    assert request.replacements[0].scope == "one"
    assert request.replacements[0].location_hint == "左上"
    assert request.replacements[1].old_text == "25.00"
    assert request.replacements[1].new_text == "26.50"
    assert request.replacements[1].scope == "ask"


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
