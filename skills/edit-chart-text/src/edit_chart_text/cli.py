"""Command-line entry point for chart text editing."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from .ocr import PaddleOCRBackend
from .pipeline import run_pipeline
from .request_io import load_request


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edit-chart-text")
    parser.add_argument("--request", required=True, help="path to UTF-8 request JSON")
    return parser


def _artifact_paths(image_path: Path) -> tuple[Path, Path, Path]:
    return (
        image_path.with_name(f"{image_path.stem}_edited.png"),
        image_path.with_name(f"{image_path.stem}_edit-report.json"),
        image_path.with_name(f"{image_path.stem}_candidates.png"),
    )


def _error(message: str) -> None:
    print(f"edit-chart-text: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the editing pipeline and return a stable process exit code."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    try:
        request = load_request(arguments.request)
    except (OSError, ValueError) as error:
        _error(f"invalid request: {error}")
        return 2

    try:
        backend = PaddleOCRBackend()
        report = run_pipeline(request, backend)
    except (ImportError, RuntimeError, OSError, ValueError) as error:
        _error(f"processing failed: {error}")
        return 4

    output_path, report_path, preview_path = _artifact_paths(request.image_path)
    if report.status == "success":
        print(f"output: {report.output_path or output_path}")
        print(f"report: {report_path}")
        return 0
    if report.status == "needs_confirmation":
        print(f"candidates: {preview_path}")
        print(f"report: {report_path}")
        return 3
    if report.status == "failed":
        for message in report.messages:
            _error(message)
        _error(f"report: {report_path}")
        return 4

    _error(f"internal protocol error: unknown report status {report.status!r}")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
