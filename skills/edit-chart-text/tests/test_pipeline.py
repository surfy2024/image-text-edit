import json

from PIL import Image, ImageDraw

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
