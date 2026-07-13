import json

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


def candidate(x: int = 10) -> TextCandidate:
    return TextCandidate(
        "HZ",
        ((x, 10), (x + 20, 10), (x + 20, 24), (x, 24)),
        0.99,
    )


class FakeOCR:
    def detect(self, image_path):
        return (candidate(),)


class AmbiguousOCR:
    def detect(self, image_path):
        return (candidate(), candidate(40))


class MultiAmbiguousOCR:
    def detect(self, image_path):
        return (
            candidate(),
            candidate(40),
            TextCandidate("P10", ((10, 26), (30, 26), (30, 38), (10, 38)), 0.99),
            TextCandidate("P10", ((40, 26), (60, 26), (60, 38), (40, 38)), 0.98),
        )


class FailingOCR:
    def detect(self, image_path):
        raise ValueError("injected OCR failure")


def chart(tmp_path):
    path = tmp_path / "chart.png"
    image = Image.new("RGB", (80, 40), "white")
    ImageDraw.Draw(image).text((10, 10), "HZ", fill="black")
    image.save(path)
    return path


def test_success_preserves_source_and_atomically_writes_output_and_report(tmp_path):
    source = chart(tmp_path)
    original = source.read_bytes()
    request = EditRequest(source, (Replacement("HZ", "CS", "one"),))

    report = run_pipeline(request, FakeOCR())

    assert report.status == "success"
    assert source.read_bytes() == original
    assert (tmp_path / "chart_edited.png").exists()
    payload = json.loads((tmp_path / "chart_edit-report.json").read_text(encoding="utf-8"))
    assert payload["status"] == "success"


def test_ambiguous_ask_writes_confirmation_report_and_preview_without_edit(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(source, (Replacement("HZ", "CS", "ask"),))

    report = run_pipeline(request, AmbiguousOCR())

    assert report.status == "needs_confirmation"
    assert not (tmp_path / "chart_edited.png").exists()
    assert (tmp_path / "chart_candidates.png").exists()
    payload = json.loads((tmp_path / "chart_edit-report.json").read_text(encoding="utf-8"))
    assert payload["status"] == "needs_confirmation"


def test_ambiguity_after_success_removes_stale_edited_output(tmp_path):
    source = chart(tmp_path)
    successful = EditRequest(source, (Replacement("HZ", "CS", "one"),))
    ambiguous = EditRequest(source, (Replacement("HZ", "CS", "ask"),))

    assert run_pipeline(successful, FakeOCR()).status == "success"
    assert (tmp_path / "chart_edited.png").exists()

    report = run_pipeline(ambiguous, AmbiguousOCR())

    assert report.status == "needs_confirmation"
    assert not (tmp_path / "chart_edited.png").exists()


def test_failure_after_success_removes_stale_edited_output(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(source, (Replacement("HZ", "CS", "one"),))

    assert run_pipeline(request, FakeOCR()).status == "success"
    report = run_pipeline(request, FailingOCR())

    assert report.status == "failed"
    assert not (tmp_path / "chart_edited.png").exists()


def test_report_publish_failure_removes_published_image_and_temp_files(
    tmp_path, monkeypatch
):
    source = chart(tmp_path)
    real_replace = pipeline.os.replace

    def fail_report_replace(source_path, destination_path):
        if destination_path.name == "chart_edit-report.json":
            raise OSError("injected report publish failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(pipeline.os, "replace", fail_report_replace)

    with pytest.raises(OSError, match="injected report publish failure"):
        run_pipeline(
            EditRequest(source, (Replacement("HZ", "CS", "one"),)), FakeOCR()
        )

    assert not (tmp_path / "chart_edited.png").exists()
    assert not tuple(tmp_path.glob(".chart_edit-report-*.tmp"))


def test_report_failure_chains_the_original_processing_error(tmp_path, monkeypatch):
    source = chart(tmp_path)
    real_replace = pipeline.os.replace

    def fail_report_replace(source_path, destination_path):
        if destination_path.name == "chart_edit-report.json":
            raise OSError("injected report publish failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(pipeline.os, "replace", fail_report_replace)

    with pytest.raises(OSError) as raised:
        run_pipeline(
            EditRequest(source, (Replacement("HZ", "CS", "one"),)), FailingOCR()
        )

    assert isinstance(raised.value.__cause__, ValueError)
    assert "injected OCR failure" in str(raised.value.__cause__)


def test_multi_replacement_confirmation_records_map_preview_numbers(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "ask"),
            Replacement("P10", "P20", "ask"),
        ),
    )

    report = run_pipeline(request, MultiAmbiguousOCR())

    assert report.status == "needs_confirmation"
    assert [item["candidate_number"] for item in report.edits] == [1, 2, 3, 4]
    assert [item["replacement_index"] for item in report.edits] == [0, 0, 1, 1]
    assert [(item["old_text"], item["new_text"]) for item in report.edits] == [
        ("HZ", "CS"),
        ("HZ", "CS"),
        ("P10", "P20"),
        ("P10", "P20"),
    ]
