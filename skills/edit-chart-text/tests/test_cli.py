import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

import edit_chart_text.cli as cli
from edit_chart_text.cli import main


def report(status, image_path, *, messages=None, output_path=None):
    return SimpleNamespace(
        status=status,
        messages=messages or [],
        output_path=output_path,
        report_path=str(Path(image_path).with_name("chart_run_edit-report.json")),
        preview_path=str(Path(image_path).with_name("chart_run_candidates.png")),
    )


def install_successful_dependencies(monkeypatch, tmp_path, status="success"):
    request = SimpleNamespace(image_path=tmp_path / "chart.png")
    backend = object()
    monkeypatch.setattr(cli, "load_request", lambda _path: request)
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: backend)
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda actual_request, actual_backend: report(
            status,
            request.image_path,
            output_path=str(tmp_path / "chart_edited.png"),
        )
        if (actual_request, actual_backend) == (request, backend)
        else None,
    )
    return request


def test_main_requires_request_argument():
    assert main([]) == 2


def test_main_reports_invalid_request(monkeypatch, capsys):
    def invalid_request(_path):
        raise ValueError("replacement list must not be empty")

    monkeypatch.setattr(cli, "load_request", invalid_request)

    assert main(["--request", "bad.json"]) == 2
    assert "replacement list must not be empty" in capsys.readouterr().err


def test_main_uses_real_request_loader_with_fake_backend(tmp_path, monkeypatch, capsys):
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "image_path": str(tmp_path / "chart.png"),
                "replacements": [{"old_text": "HZ", "new_text": "CS"}],
            }
        ),
        encoding="utf-8",
    )
    backend = object()
    seen = {}
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: backend)

    def fake_pipeline(request, actual_backend):
        seen["request"] = request
        seen["backend"] = actual_backend
        return report("success", request.image_path, output_path=str(tmp_path / "chart_edited.png"))

    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline)

    assert main(["--request", str(request_path)]) == 0
    assert seen["request"].replacements[0].old_text == "HZ"
    assert seen["backend"] is backend
    output = capsys.readouterr().out
    assert "chart_edited.png" in output
    assert "edit-report.json" in output


@pytest.mark.parametrize(
    "error",
    [ImportError("paddle unavailable"), ModuleNotFoundError("paddleocr"), RuntimeError("model failed")],
)
def test_main_reports_backend_initialization_errors(monkeypatch, capsys, error):
    monkeypatch.setattr(
        cli,
        "load_request",
        lambda _path: SimpleNamespace(image_path=Path("chart.png")),
    )

    def fail_backend():
        raise error

    monkeypatch.setattr(cli, "PaddleOCRBackend", fail_backend)

    assert main(["--request", "request.json"]) == 4
    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize("error", [OSError("OCR unavailable"), ValueError("bad OCR result"), RuntimeError("model failed")])
def test_main_reports_pipeline_runtime_errors(monkeypatch, capsys, error):
    monkeypatch.setattr(
        cli,
        "load_request",
        lambda _path: SimpleNamespace(image_path=Path("chart.png")),
    )
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: object())

    def fail_pipeline(_request, _backend):
        raise error

    monkeypatch.setattr(cli, "run_pipeline", fail_pipeline)

    assert main(["--request", "request.json"]) == 4
    assert str(error) in capsys.readouterr().err


def test_main_failed_report_prints_messages_and_report_path(monkeypatch, capsys, tmp_path):
    request = install_successful_dependencies(monkeypatch, tmp_path, "failed")
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda _request, _backend: report(
            "failed", request.image_path, messages=["安全校验失败"]
        ),
    )

    assert main(["--request", "request.json"]) == 4
    error = capsys.readouterr().err
    assert "安全校验失败" in error
    assert "edit-report.json" in error


def test_main_needs_confirmation_prints_report_and_candidates_paths(monkeypatch, capsys, tmp_path):
    install_successful_dependencies(monkeypatch, tmp_path, "needs_confirmation")

    assert main(["--request", "request.json"]) == 3
    output = capsys.readouterr().out
    assert "_candidates.png" in output
    assert "edit-report.json" in output


def test_main_unknown_status_is_protocol_error(monkeypatch, capsys, tmp_path):
    install_successful_dependencies(monkeypatch, tmp_path, "surprise")

    assert main(["--request", "request.json"]) == 4
    assert "protocol" in capsys.readouterr().err.lower()


def test_main_rejects_selection_outside_scope_one_before_backend(
    tmp_path, monkeypatch, capsys
):
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "image_path": str(tmp_path / "chart.png"),
                "replacements": [
                    {
                        "old_text": "HZ",
                        "new_text": "CS",
                        "scope": "ask",
                        "candidate_number": 1,
                        "candidate_polygon": [
                            [10, 10],
                            [30, 10],
                            [30, 24],
                            [10, 24],
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def backend_must_not_run():
        raise AssertionError("backend must not run for invalid request")

    monkeypatch.setattr(cli, "PaddleOCRBackend", backend_must_not_run)

    assert main(["--request", str(request_path)]) == 2
    assert "scope=one" in capsys.readouterr().err


def test_main_wraps_paddle_constructor_failure_without_traceback(
    tmp_path, monkeypatch, capsys
):
    image_path = tmp_path / "chart.png"
    Image.new("RGB", (20, 20), "white").save(image_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "image_path": image_path.name,
                "replacements": [
                    {"old_text": "HZ", "new_text": "CS", "scope": "one"}
                ],
            }
        ),
        encoding="utf-8",
    )

    class BrokenEngine:
        def __init__(self, **kwargs):
            raise Exception("No available model hosting platforms detected")

    monkeypatch.setitem(
        sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=BrokenEngine)
    )

    assert main(["--request", str(request_path)]) == 4
    error = capsys.readouterr().err
    assert "OCR initialization or model acquisition failed" in error
    assert "models/cache or network access" in error
    assert "Traceback" not in error
