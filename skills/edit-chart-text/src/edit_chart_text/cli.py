"""Command-line entry point for chart text editing."""

import argparse
from collections.abc import Sequence
import sys

from .ocr import PaddleOCRBackend
from .pipeline import run_pipeline
from .request_io import load_request


_EXIT_CODES = {
    "success": 0,
    "needs_confirmation": 3,
    "failed": 4,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edit-chart-text")
    parser.add_argument("--request", required=True, help="path to UTF-8 request JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the editing pipeline and return a stable process exit code."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    try:
        request = load_request(arguments.request)
        report = run_pipeline(request, PaddleOCRBackend())
    except (OSError, ValueError) as error:
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 2

    return _EXIT_CODES.get(report.status, 4)


if __name__ == "__main__":
    raise SystemExit(main())
