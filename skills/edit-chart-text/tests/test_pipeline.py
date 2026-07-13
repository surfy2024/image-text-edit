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


class FuzzyOCR:
    def detect(self, image_path):
        item = candidate()
        return (TextCandidate("HZZ", item.polygon, item.confidence),)


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


class OverlappingOCR:
    def detect(self, image_path):
        return (
            candidate(),
            TextCandidate(
                "P10", ((25, 12), (45, 12), (45, 26), (25, 26)), 0.99
            ),
        )


class SameScopeOverlappingOCR:
    def detect(self, image_path):
        return (candidate(), candidate(25))


class PaddingOnlyOverlapOCR:
    def detect(self, image_path):
        return (
            candidate(),
            TextCandidate(
                "P10", ((33, 10), (53, 10), (53, 24), (33, 24)), 0.99
            ),
        )


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


def test_same_candidate_selected_by_two_replacements_needs_confirmation(tmp_path):
    source = chart(tmp_path)
    original = source.read_bytes()
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one"),
            Replacement("HZ", "AB", "one"),
        ),
    )

    report = run_pipeline(request, FakeOCR())

    assert report.status == "needs_confirmation"
    assert source.read_bytes() == original
    assert not (tmp_path / "chart_edited.png").exists()
    assert (tmp_path / "chart_candidates.png").exists()
    assert [item["replacement_index"] for item in report.edits] == [0, 1]
    assert any("冲突" in message for message in report.messages)


def test_overlapping_candidates_across_replacements_need_confirmation(tmp_path):
    source = chart(tmp_path)
    original = source.read_bytes()
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one"),
            Replacement("P10", "P20", "one"),
        ),
    )

    report = run_pipeline(request, OverlappingOCR())

    assert report.status == "needs_confirmation"
    assert source.read_bytes() == original
    assert not (tmp_path / "chart_edited.png").exists()
    assert (tmp_path / "chart_candidates.png").exists()
    assert [item["candidate_number"] for item in report.edits] == [1, 2]
    assert [item["replacement_index"] for item in report.edits] == [0, 1]


def test_success_removes_preview_from_previous_confirmation(tmp_path):
    source = chart(tmp_path)
    ambiguous = EditRequest(source, (Replacement("HZ", "CS", "ask"),))
    successful = EditRequest(source, (Replacement("HZ", "CS", "one"),))

    assert run_pipeline(ambiguous, AmbiguousOCR()).status == "needs_confirmation"
    assert (tmp_path / "chart_candidates.png").exists()

    assert run_pipeline(successful, FakeOCR()).status == "success"
    assert not (tmp_path / "chart_candidates.png").exists()


def test_run_does_not_delete_another_runs_unique_temp_file(tmp_path):
    source = chart(tmp_path)
    concurrent_temp = tmp_path / ".chart_edited-other-run.tmp"
    concurrent_temp.write_bytes(b"in progress")

    report = run_pipeline(
        EditRequest(source, (Replacement("HZ", "CS", "one"),)), FakeOCR()
    )

    assert report.status == "success"
    assert concurrent_temp.read_bytes() == b"in progress"


def test_overlapping_candidates_within_one_all_replacement_need_confirmation(tmp_path):
    source = chart(tmp_path)
    original = source.read_bytes()
    request = EditRequest(source, (Replacement("HZ", "CS", "all"),))

    report = run_pipeline(request, SameScopeOverlappingOCR())

    assert report.status == "needs_confirmation"
    assert source.read_bytes() == original
    assert not (tmp_path / "chart_edited.png").exists()
    assert (tmp_path / "chart_candidates.png").exists()
    assert [item["candidate_number"] for item in report.edits] == [1, 2]


def test_padding_overlap_needs_confirmation_before_any_edit(tmp_path):
    source = chart(tmp_path)
    original = source.read_bytes()
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one"),
            Replacement("P10", "P20", "one"),
        ),
    )

    report = run_pipeline(request, PaddingOnlyOverlapOCR())

    assert report.status == "needs_confirmation"
    assert source.read_bytes() == original
    assert not (tmp_path / "chart_edited.png").exists()
    assert (tmp_path / "chart_candidates.png").exists()
    assert [item["replacement_index"] for item in report.edits] == [0, 1]


def test_candidate_number_closes_ambiguity_loop_and_edits_only_selected_candidate(
    tmp_path,
):
    source = chart(tmp_path)
    initial = EditRequest(source, (Replacement("HZ", "CS", "ask"),))

    first_report = run_pipeline(initial, AmbiguousOCR())
    first_payload = json.loads(
        (tmp_path / "chart_edit-report.json").read_text(encoding="utf-8")
    )
    selected = first_payload["edits"][1]

    assert first_report.status == "needs_confirmation"
    assert selected["candidate_number"] == 2

    confirmed = EditRequest(
        source,
        (Replacement("HZ", "CS", "one", candidate_number=2),),
    )
    second_report = run_pipeline(confirmed, AmbiguousOCR())

    second_payload = json.loads(
        (tmp_path / "chart_edit-report.json").read_text(encoding="utf-8")
    )
    expected_polygon = [list(point) for point in candidate(40).polygon]
    assert second_report.status == "success"
    assert len(second_report.edits) == 1
    assert len(second_payload["edits"]) == 1
    assert second_report.edits[0]["polygon"] == selected["polygon"]
    assert second_payload["edits"][0]["polygon"] == expected_polygon


def test_unknown_candidate_number_does_not_edit(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(
        source,
        (Replacement("HZ", "CS", "one", candidate_number=99),),
    )

    report = run_pipeline(request, AmbiguousOCR())

    assert report.status == "needs_confirmation"
    assert not (tmp_path / "chart_edited.png").exists()
    assert any("99" in message and "不存在" in message for message in report.messages)


def test_candidate_number_owned_by_other_replacement_does_not_edit(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one", candidate_number=3),
            Replacement("P10", "P20", "ask"),
        ),
    )

    report = run_pipeline(request, MultiAmbiguousOCR())

    assert report.status == "needs_confirmation"
    assert not (tmp_path / "chart_edited.png").exists()
    assert any("3" in message and "不属于" in message for message in report.messages)


def test_conflict_candidate_number_survives_replacement_list_reduction(tmp_path):
    source = chart(tmp_path)
    initial = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one"),
            Replacement("P10", "P20", "one"),
        ),
    )

    first_report = run_pipeline(initial, OverlappingOCR())
    selected = next(
        item for item in first_report.edits if item["replacement_index"] == 1
    )

    assert first_report.status == "needs_confirmation"
    assert selected["candidate_number"] == 2

    confirmed = EditRequest(
        source,
        (
            Replacement(
                "P10",
                "P20",
                "one",
                candidate_number=selected["candidate_number"],
            ),
        ),
    )
    second_report = run_pipeline(confirmed, OverlappingOCR())

    assert second_report.status == "success"
    assert len(second_report.edits) == 1
    assert second_report.edits[0]["polygon"] == selected["polygon"]


def test_same_ocr_candidate_has_same_global_number_across_replacements(tmp_path):
    source = chart(tmp_path)
    request = EditRequest(
        source,
        (
            Replacement("HZ", "CS", "one"),
            Replacement("HZ", "AB", "one"),
        ),
    )

    report = run_pipeline(request, FakeOCR())

    assert report.status == "needs_confirmation"
    assert [item["candidate_number"] for item in report.edits] == [1, 1]
    assert [item["replacement_index"] for item in report.edits] == [0, 1]


def test_fuzzy_candidate_only_becomes_ready_after_explicit_global_number(tmp_path):
    source = chart(tmp_path)
    initial = EditRequest(source, (Replacement("HZ", "CS", "one"),))

    first_report = run_pipeline(initial, FuzzyOCR())

    assert first_report.status == "needs_confirmation"
    assert first_report.edits[0]["candidate_number"] == 1

    confirmed = EditRequest(
        source,
        (Replacement("HZ", "CS", "one", candidate_number=1),),
    )
    second_report = run_pipeline(confirmed, FuzzyOCR())

    assert second_report.status == "success"
    assert len(second_report.edits) == 1
    assert second_report.edits[0]["polygon"] == first_report.edits[0]["polygon"]
