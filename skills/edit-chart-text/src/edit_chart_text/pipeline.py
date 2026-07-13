"""Safe orchestration for OCR-guided chart text replacement."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from .matching import MatchDecision, choose_candidates
from .models import EditReport, EditRequest, Replacement, TextCandidate
from .repair import repair_region
from .render import render_replacement
from .style import estimate_style
from .validate import unchanged_outside


def _paths(source: Path) -> tuple[Path, Path, Path]:
    directory = source.parent
    return (
        directory / f"{source.stem}_edited.png",
        directory / f"{source.stem}_edit-report.json",
        directory / f"{source.stem}_candidates.png",
    )


def _write_report(path: Path, report: EditReport) -> None:
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_image_atomically(image: Image.Image, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        image.save(temporary, format="PNG")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _candidate_record(number: int, candidate: TextCandidate) -> dict:
    return {
        "candidate_number": number,
        "text": candidate.text,
        "confidence": candidate.confidence,
        "polygon": [list(point) for point in candidate.polygon],
    }


def _write_preview(
    source: Image.Image,
    candidates: tuple[TextCandidate, ...],
    destination: Path,
) -> None:
    preview = source.convert("RGB").copy()
    draw = ImageDraw.Draw(preview)
    for number, candidate in enumerate(candidates, start=1):
        points = list(candidate.polygon)
        if len(points) >= 2:
            draw.line(points + [points[0]], fill=(255, 0, 0), width=2)
            x, y = points[0]
            draw.text((x + 2, max(0, y - 11)), str(number), fill=(255, 0, 0), stroke_width=1, stroke_fill="white")
    _write_image_atomically(preview, destination)


def _confirmation_report(
    image: Image.Image,
    decisions: list[tuple[Replacement, MatchDecision]],
    preview_path: Path,
) -> EditReport:
    candidates: list[TextCandidate] = []
    messages: list[str] = []
    for replacement, decision in decisions:
        if decision.status != "ready":
            messages.append(
                f"{replacement.old_text!r} -> {replacement.new_text!r}: {decision.status}"
            )
            candidates.extend(decision.candidates)
    numbered = tuple(candidates)
    _write_preview(image, numbered, preview_path)
    messages.append(f"候选预览：{preview_path}")
    return EditReport(
        status="needs_confirmation",
        messages=messages,
        edits=[_candidate_record(index, item) for index, item in enumerate(numbered, 1)],
    )


def run_pipeline(request: EditRequest, ocr_backend) -> EditReport:
    """Run requested edits without ever overwriting the source image."""
    source_path = Path(request.image_path)
    output_path, report_path, preview_path = _paths(source_path)

    try:
        with Image.open(source_path) as opened:
            opened.load()
            source = opened.convert("RGB")

        detected = tuple(ocr_backend.detect(source_path))
        decisions = [
            (replacement, choose_candidates(replacement, detected))
            for replacement in request.replacements
        ]
        if any(decision.status != "ready" for _, decision in decisions):
            report = _confirmation_report(source, decisions, preview_path)
            _write_report(report_path, report)
            return report

        working = source.copy()
        allowed_boxes: list[tuple[int, int, int, int]] = []
        edits: list[dict] = []
        for replacement, decision in decisions:
            for candidate in decision.candidates:
                style = estimate_style(source, candidate)
                working, allowed, method = repair_region(working, candidate)
                working = render_replacement(
                    working, candidate, replacement.new_text, style, allowed
                )
                allowed_boxes.append(allowed)
                edits.append(
                    {
                        "old_text": replacement.old_text,
                        "new_text": replacement.new_text,
                        "confidence": candidate.confidence,
                        "polygon": [list(point) for point in candidate.polygon],
                        "allowed_box": list(allowed),
                        "repair_method": method,
                    }
                )

        if not unchanged_outside(source, working, allowed_boxes):
            report = EditReport(
                status="failed",
                messages=["安全校验失败：允许区域外的像素发生变化。"],
                edits=edits,
            )
            _write_report(report_path, report)
            return report

        _write_image_atomically(working, output_path)
        report = EditReport(
            status="success",
            output_path=str(output_path),
            messages=["编辑完成，源图未被覆盖。"],
            edits=edits,
        )
        _write_report(report_path, report)
        return report
    except Exception as error:
        report = EditReport(status="failed", messages=[f"处理失败：{error}"])
        _write_report(report_path, report)
        return report
