from pathlib import Path
from typing import get_type_hints

from edit_chart_text.models import EditReport, Replacement, TextCandidate, TextStyle


def test_model_field_types_match_pipeline_contract() -> None:
    replacement_hints = get_type_hints(Replacement)
    candidate_hints = get_type_hints(TextCandidate)
    style_hints = get_type_hints(TextStyle)
    report_hints = get_type_hints(EditReport)

    assert replacement_hints["candidate_number"] == int | None
    assert replacement_hints["candidate_polygon"] == tuple[tuple[int, int], ...] | None
    assert replacement_hints["candidate_token"] == str | None
    assert candidate_hints["polygon"] == tuple[tuple[int, int], ...]
    assert style_hints["font_size"] is int
    assert report_hints["output_path"] == str | None
    assert report_hints["edits"] == list[dict]
    assert report_hints["run_id"] == str
    assert report_hints["source_sha256"] == str
    assert report_hints["report_path"] == str | None
    assert report_hints["preview_path"] == str | None


def test_edit_report_has_independent_mutable_default_containers() -> None:
    first = EditReport(status="success")
    second = EditReport(status="failed")

    first.messages.append("done")
    first.edits.append({"old_text": "P10", "new_text": "P40"})

    assert first.output_path is None
    assert second.messages == []
    assert second.edits == []
