"""Load chart text edit requests from JSON files."""

import json
from pathlib import Path
from typing import Any
from .models import EditRequest, Replacement

_VALID_SCOPES = {"one", "all", "ask"}
_VALID_MATCH_MODES = {"exact", "substring"}

def _resolved(value: str, base: Path) -> Path:
    path = Path(value.strip())
    return path if path.is_absolute() else (base / path).resolve()

def load_request(path: str | Path) -> EditRequest:
    request_path = Path(path)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")
    image_path = payload.get("image_path")
    if not isinstance(image_path, str) or not image_path.strip():
        raise ValueError("image_path must be a non-empty string")
    raw = payload.get("replacements")
    if not isinstance(raw, list):
        raise ValueError("replacements must be a list")
    if not raw:
        raise ValueError("replacement list must not be empty")
    report_value = payload.get("confirmation_report_path")
    if report_value is not None and (not isinstance(report_value, str) or not report_value.strip()):
        raise ValueError("confirmation_report_path must be a non-empty string")
    replacements = tuple(_load_replacement(item, index) for index, item in enumerate(raw))
    if report_value is None:
        for index, replacement in enumerate(replacements):
            if replacement.substring_occurrence is not None:
                raise ValueError(
                    f"replacements[{index}].substring_occurrence requires confirmation_report_path"
                )
    has_selection = any(item.candidate_number is not None for item in replacements)
    if has_selection and report_value is None:
        raise ValueError("confirmation_report_path is required for candidate selection")
    if report_value is not None and not has_selection:
        raise ValueError("confirmation_report_path requires candidate selection fields")
    base = request_path.parent
    return EditRequest(
        image_path=_resolved(image_path, base),
        replacements=replacements,
        confirmation_report_path=_resolved(report_value, base) if report_value is not None else None,
    )

def _load_candidate_polygon(value: Any, context: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or len(value) < 4:
        raise ValueError(f"{context}.candidate_polygon must contain at least 4 points")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or type(point[0]) is not int or type(point[1]) is not int:
            raise ValueError(f"{context}.candidate_polygon points must be integer pairs")
        points.append((point[0], point[1]))
    if len(set(points)) < 4:
        raise ValueError(f"{context}.candidate_polygon must not be degenerate")
    twice_area = abs(sum(x1*y2-x2*y1 for (x1,y1),(x2,y2) in zip(points, points[1:]+points[:1])))
    if twice_area == 0:
        raise ValueError(f"{context}.candidate_polygon must not be degenerate")
    return tuple(points)

def _load_replacement(item: Any, index: int) -> Replacement:
    context = f"replacements[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{context} must be an object")
    old_text, new_text = item.get("old_text"), item.get("new_text")
    if not isinstance(old_text, str) or not old_text.strip() or not isinstance(new_text, str) or not new_text.strip():
        raise ValueError(f"{context}.old_text and new_text must both be non-empty strings")
    scope = item.get("scope", "ask")
    if not isinstance(scope, str) or scope not in _VALID_SCOPES:
        raise ValueError(f"{context}.scope must be one of: one, all, ask")
    match_mode = item.get("match_mode", "exact")
    if not isinstance(match_mode, str) or match_mode not in _VALID_MATCH_MODES:
        raise ValueError(f"{context}.match_mode must be one of: exact, substring")
    if match_mode == "substring" and old_text.strip() == new_text.strip():
        raise ValueError(f"{context}.old_text and new_text must differ in substring mode")
    hint = item.get("location_hint")
    if hint is not None:
        if not isinstance(hint, str) or not hint.strip():
            raise ValueError(f"{context}.location_hint must be null or a non-empty string")
        hint = hint.strip()
    number_present = "candidate_number" in item
    polygon_present = "candidate_polygon" in item
    token_present = "candidate_token" in item
    occurrence_present = "substring_occurrence" in item
    occurrence = item.get("substring_occurrence")
    if occurrence_present and (type(occurrence) is not int or occurrence <= 0):
        raise ValueError(f"{context}.substring_occurrence must be a positive integer")
    if occurrence_present and match_mode != "substring":
        raise ValueError(f"{context}.substring_occurrence requires match_mode=substring")
    if occurrence_present and not (number_present and polygon_present and token_present):
        raise ValueError(f"{context}.substring_occurrence requires candidate selection fields")
    number = item.get("candidate_number")
    polygon = token = None
    if number_present and (type(number) is not int or number <= 0):
        raise ValueError(f"{context}.candidate_number must be a positive report number")
    if number_present != polygon_present:
        raise ValueError(f"{context}.candidate_number and candidate_polygon must appear together")
    if token_present and not number_present:
        raise ValueError(f"{context}.candidate_token must appear with candidate selection fields")
    if number_present:
        polygon = _load_candidate_polygon(item.get("candidate_polygon"), context)
        if scope != "one":
            raise ValueError(f"{context}.candidate selection requires scope=one")
        if not token_present:
            raise ValueError(f"{context}.candidate_token must appear with candidate selection fields")
        token = item.get("candidate_token")
        if not isinstance(token, str) or len(token) < 16:
            raise ValueError(f"{context}.candidate_token must be a non-empty high-entropy token")
    return Replacement(
        old_text=old_text.strip(),
        new_text=new_text.strip(),
        scope=scope,
        location_hint=hint,
        candidate_number=number,
        candidate_polygon=polygon,
        candidate_token=token,
        match_mode=match_mode,
        substring_occurrence=occurrence,
    )
