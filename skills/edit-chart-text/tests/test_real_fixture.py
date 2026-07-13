from pathlib import Path

from edit_chart_text.ocr import PaddleOCRBackend
from edit_chart_text.pipeline import run_pipeline
from edit_chart_text.request_io import load_request


FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "chart_sample.png"
REQUEST = FIXTURES / "chart_sample_request.json"
GENERATED = (
    FIXTURES / "chart_sample_edited.png",
    FIXTURES / "chart_sample_edit-report.json",
    FIXTURES / "chart_sample_candidates.png",
)


def test_real_chart_fixture_runs_with_paddleocr_without_mutating_source(request):
    original = SOURCE.read_bytes()
    request.addfinalizer(lambda: [path.unlink(missing_ok=True) for path in GENERATED])

    edit_request = load_request(REQUEST)
    report = run_pipeline(edit_request, PaddleOCRBackend())

    assert SOURCE.read_bytes() == original
    assert report.status in {"success", "needs_confirmation"}, report.messages
