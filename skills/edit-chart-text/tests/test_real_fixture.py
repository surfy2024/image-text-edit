import json
import os
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
import pytest

from edit_chart_text.ocr import PaddleOCRBackend
from edit_chart_text.pipeline import EDIT_PADDING, run_pipeline
from edit_chart_text.request_io import load_request


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_FIXTURE = FIXTURES / "chart_sample.png"
REQUEST_FIXTURE = FIXTURES / "chart_sample_request.json"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_REAL_OCR") != "1",
        reason="set RUN_REAL_OCR=1 to run the real PaddleOCR integration test",
    ),
]


def _write_request(path: Path, image_name: str, old_text: str, new_text: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "image_path": image_name,
                "replacements": [
                    {
                        "old_text": old_text,
                        "new_text": new_text,
                        "scope": "one",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_real_chart_fixture_detects_and_edits_without_mutating_source(tmp_path):
    source = tmp_path / "chart_sample.png"
    request_path = tmp_path / "chart_sample_request.json"
    shutil.copyfile(SOURCE_FIXTURE, source)
    shutil.copyfile(REQUEST_FIXTURE, request_path)
    original = source.read_bytes()

    request = load_request(request_path)
    assert request.image_path == source

    backend = PaddleOCRBackend()
    candidates = backend.detect(source)
    matching = tuple(
        candidate
        for candidate in candidates
        if candidate.text == "HYSY FPSO" and candidate.confidence >= 0.90
    )
    assert matching, [
        (candidate.text, candidate.confidence) for candidate in candidates
    ]

    not_found = run_pipeline(request, backend)
    assert not_found.status == "needs_confirmation", not_found.messages
    assert not_found.edits == []
    assert source.read_bytes() == original
    assert not (tmp_path / "chart_sample_edited.png").exists()

    edit_request_path = _write_request(
        tmp_path / "chart_sample_edit_request.json",
        source.name,
        "HYSY FPSO",
        "CS",
    )
    edited_report = run_pipeline(load_request(edit_request_path), backend)

    edited_path = tmp_path / "chart_sample_edited.png"
    report_path = tmp_path / "chart_sample_edit-report.json"
    assert edited_report.status == "success", edited_report.messages
    assert source.read_bytes() == original
    assert edited_path.exists()
    assert report_path.exists()
    assert len(edited_report.edits) == 1

    edit = edited_report.edits[0]
    edit_polygon = tuple(tuple(point) for point in edit["polygon"])
    assert edit_polygon in {candidate.polygon for candidate in matching}
    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_payload["status"] == "success"
    assert report_payload["edits"][0]["polygon"] == edit["polygon"]

    with Image.open(source) as before_image, Image.open(edited_path) as after_image:
        before = np.asarray(before_image.convert("RGB"))
        after = np.asarray(after_image.convert("RGB"))
    changed = np.any(before != after, axis=2)
    assert np.any(changed)

    height, width = changed.shape
    xs = [point[0] for point in edit_polygon]
    ys = [point[1] for point in edit_polygon]
    left = max(0, min(xs) - EDIT_PADDING)
    top = max(0, min(ys) - EDIT_PADDING)
    right = min(width, max(xs) + EDIT_PADDING)
    bottom = min(height, max(ys) + EDIT_PADDING)
    approved = np.zeros_like(changed, dtype=bool)
    approved[top:bottom, left:right] = True
    assert not np.any(changed & ~approved)
