from types import SimpleNamespace

import pytest

import edit_chart_text.cli as cli
from edit_chart_text.cli import main


def test_main_requires_request_argument():
    assert main([]) == 2


def test_main_reports_invalid_request(monkeypatch, capsys):
    def invalid_request(_path):
        raise ValueError("replacement list must not be empty")

    monkeypatch.setattr(cli, "load_request", invalid_request)

    assert main(["--request", "bad.json"]) == 2
    assert "replacement list must not be empty" in capsys.readouterr().err


def test_main_reports_pipeline_io_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_request", lambda _path: object())
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: object())

    def unavailable(_request, _backend):
        raise OSError("OCR model unavailable")

    monkeypatch.setattr(cli, "run_pipeline", unavailable)

    assert main(["--request", "request.json"]) == 2
    assert "OCR model unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("success", 0), ("needs_confirmation", 3), ("failed", 4)],
)
def test_main_maps_report_status_to_exit_code(
    monkeypatch, status, exit_code
):
    request = object()
    backend = object()
    monkeypatch.setattr(cli, "load_request", lambda _path: request)
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: backend)
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda actual_request, actual_backend: (
            SimpleNamespace(status=status)
            if (actual_request, actual_backend) == (request, backend)
            else None
        ),
    )

    assert main(["--request", "request.json"]) == exit_code
