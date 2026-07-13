"""Load chart text edit requests from JSON files."""

import json
from pathlib import Path
from typing import Any

from .models import EditRequest, Replacement


_VALID_SCOPES = {"one", "all", "ask"}


def load_request(path: str | Path) -> EditRequest:
    """Load and validate an edit request encoded as UTF-8 JSON."""
    request_path = Path(path)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")

    image_path = payload.get("image_path")
    if not isinstance(image_path, str) or not image_path.strip():
        raise ValueError("image_path must be a non-empty string")

    raw_replacements = payload.get("replacements")
    if not isinstance(raw_replacements, list):
        raise ValueError("replacements must be a list")
    if not raw_replacements:
        raise ValueError("replacement list must not be empty")

    replacements = tuple(
        _load_replacement(item, index) for index, item in enumerate(raw_replacements)
    )
    return EditRequest(image_path=Path(image_path.strip()), replacements=replacements)


def _load_replacement(item: Any, index: int) -> Replacement:
    context = f"replacements[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{context} must be an object")

    old_text = item.get("old_text")
    new_text = item.get("new_text")
    if (
        not isinstance(old_text, str)
        or not old_text.strip()
        or not isinstance(new_text, str)
        or not new_text.strip()
    ):
        raise ValueError(
            f"{context}.old_text and new_text must both be non-empty strings"
        )

    scope = item.get("scope", "ask")
    if not isinstance(scope, str) or scope not in _VALID_SCOPES:
        raise ValueError(f"{context}.scope must be one of: one, all, ask")

    location_hint = item.get("location_hint")
    if location_hint is not None:
        if not isinstance(location_hint, str) or not location_hint.strip():
            raise ValueError(
                f"{context}.location_hint must be null or a non-empty string"
            )
        location_hint = location_hint.strip()

    candidate_number = item.get("candidate_number")
    if "candidate_number" in item and (
        type(candidate_number) is not int or candidate_number <= 0
    ):
        raise ValueError(
            f"{context}.candidate_number must be a positive "
            "1-based global OCR candidate number"
        )

    return Replacement(
        old_text=old_text.strip(),
        new_text=new_text.strip(),
        scope=scope,
        location_hint=location_hint,
        candidate_number=candidate_number,
    )
