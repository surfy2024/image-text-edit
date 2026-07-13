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
from .style import candidate_bounds, estimate_style
from .validate import unchanged_outside


EDIT_PADDING = 2
CandidateEntry = tuple[int, int, Replacement, TextCandidate]


def _paths(source: Path) -> tuple[Path, Path, Path]:
    directory = source.parent
    return (
        directory / f"{source.stem}_edited.png",
        directory / f"{source.stem}_edit-report.json",
        directory / f"{source.stem}_candidates.png",
    )


def _write_report(path: Path, report: EditReport) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(asdict(report), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_stale_artifacts(output_path: Path, preview_path: Path) -> None:
    output_path.unlink(missing_ok=True)
    preview_path.unlink(missing_ok=True)


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


def _candidate_record(
    number: int,
    replacement_index: int,
    replacement: Replacement,
    candidate: TextCandidate,
) -> dict:
    return {
        "candidate_number": number,
        "replacement_index": replacement_index,
        "old_text": replacement.old_text,
        "new_text": replacement.new_text,
        "text": candidate.text,
        "confidence": candidate.confidence,
        "polygon": [list(point) for point in candidate.polygon],
    }


def _write_preview(
    source: Image.Image,
    candidates: tuple[tuple[int, TextCandidate], ...],
    destination: Path,
) -> None:
    preview = source.convert("RGB").copy()
    draw = ImageDraw.Draw(preview)
    labels: dict[tuple[tuple[int, int], ...], set[int]] = {}
    for number, candidate in candidates:
        labels.setdefault(candidate.polygon, set()).add(number)
    for polygon, numbers in labels.items():
        points = list(polygon)
        if len(points) >= 2:
            draw.line(points + [points[0]], fill=(255, 0, 0), width=2)
            x, y = points[0]
            draw.text(
                (x + 2, max(0, y - 11)),
                ",".join(str(number) for number in sorted(numbers)),
                fill=(255, 0, 0),
                stroke_width=1,
                stroke_fill="white",
            )
    _write_image_atomically(preview, destination)


def _candidate_preview_report(
    image: Image.Image,
    entries: list[CandidateEntry],
    preview_path: Path,
    messages: list[str],
) -> EditReport:
    candidates = tuple((number, candidate) for number, _, _, candidate in entries)
    _write_preview(image, candidates, preview_path)
    messages.append(f"候选预览：{preview_path}")
    return EditReport(
        status="needs_confirmation",
        messages=messages,
        edits=[
            _candidate_record(number, replacement_index, replacement, candidate)
            for number, replacement_index, replacement, candidate in entries
        ],
    )


def _global_candidate_numbers(
    candidates: tuple[TextCandidate, ...],
) -> dict[int, int]:
    numbers: dict[int, int] = {}
    for number, candidate in enumerate(candidates, 1):
        numbers.setdefault(id(candidate), number)
    return numbers


def _numbered_entries(
    decisions: list[tuple[Replacement, MatchDecision]],
    global_numbers: dict[int, int],
    *,
    include_ready: bool,
) -> list[CandidateEntry]:
    return [
        (global_numbers[id(candidate)], replacement_index, replacement, candidate)
        for replacement_index, (replacement, decision) in enumerate(decisions)
        if include_ready or decision.status != "ready"
        for candidate in decision.candidates
    ]


def _apply_candidate_numbers(
    decisions: list[tuple[Replacement, MatchDecision]],
    detected: tuple[TextCandidate, ...],
) -> tuple[list[tuple[Replacement, MatchDecision]], list[str]]:
    resolved: list[tuple[Replacement, MatchDecision]] = []
    messages: list[str] = []
    for replacement_index, (replacement, decision) in enumerate(decisions):
        number = replacement.candidate_number
        if number is None:
            resolved.append((replacement, decision))
            continue
        if number > len(detected):
            messages.append(
                f"replacement[{replacement_index}] 的候选编号 {number} 不存在。"
            )
            resolved.append(
                (replacement, MatchDecision("needs_confirmation", decision.candidates))
            )
            continue

        selected = detected[number - 1]
        if not any(selected is candidate for candidate in decision.candidates):
            messages.append(
                f"候选编号 {number} 不属于 replacement[{replacement_index}]。"
            )
            resolved.append(
                (replacement, MatchDecision("needs_confirmation", decision.candidates))
            )
        else:
            resolved.append((replacement, MatchDecision("ready", (selected,))))
    return resolved, messages

def _confirmation_report(
    image: Image.Image,
    decisions: list[tuple[Replacement, MatchDecision]],
    global_numbers: dict[int, int],
    preview_path: Path,
    selection_messages: list[str] | None = None,
) -> EditReport:
    entries = _numbered_entries(
        decisions, global_numbers, include_ready=False
    )
    messages = list(selection_messages or [])
    for replacement_index, (replacement, decision) in enumerate(decisions):
        if decision.status != "ready":
            messages.append(
                f"{replacement.old_text!r} -> {replacement.new_text!r}: {decision.status}"
            )
    return _candidate_preview_report(image, entries, preview_path, messages)

def _planned_edit_bounds(
    candidate: TextCandidate,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = candidate_bounds(candidate, image_size)
    width, height = image_size
    return (
        max(0, left - EDIT_PADDING),
        max(0, top - EDIT_PADDING),
        min(width, right + EDIT_PADDING),
        min(height, bottom + EDIT_PADDING),
    )


def _overlap(
    left: TextCandidate,
    right: TextCandidate,
    image_size: tuple[int, int],
) -> bool:
    left_l, left_t, left_r, left_b = _planned_edit_bounds(left, image_size)
    right_l, right_t, right_r, right_b = _planned_edit_bounds(right, image_size)
    return (
        min(left_r, right_r) > max(left_l, right_l)
        and min(left_b, right_b) > max(left_t, right_t)
    )


def _conflict_report(
    image: Image.Image,
    decisions: list[tuple[Replacement, MatchDecision]],
    global_numbers: dict[int, int],
    preview_path: Path,
) -> EditReport | None:
    entries = _numbered_entries(
        decisions, global_numbers, include_ready=True
    )
    conflicting: set[int] = set()
    messages: list[str] = []
    for left_position, left in enumerate(entries):
        for right_position in range(left_position + 1, len(entries)):
            right = entries[right_position]
            if not _overlap(left[3], right[3], image.size):
                continue
            conflicting.update((left_position, right_position))
            messages.append(
                "候选区域冲突："
                f"replacement[{left[1]}] {left[2].old_text!r}->{left[2].new_text!r} "
                f"与 replacement[{right[1]}] "
                f"{right[2].old_text!r}->{right[2].new_text!r} 重叠。"
            )
    if not conflicting:
        return None
    conflict_entries = [
        entry for index, entry in enumerate(entries) if index in conflicting
    ]
    return _candidate_preview_report(
        image, conflict_entries, preview_path, messages
    )

def _ready_report(
    source: Image.Image,
    decisions: list[tuple[Replacement, MatchDecision]],
    output_path: Path,
) -> tuple[EditReport, bool]:
    working = source.copy()
    allowed_boxes: list[tuple[int, int, int, int]] = []
    edits: list[dict] = []
    for replacement, decision in decisions:
        for candidate in decision.candidates:
            style = estimate_style(source, candidate)
            working, allowed, method = repair_region(
                working, candidate, padding=EDIT_PADDING
            )
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
        return (
            EditReport(
                status="failed",
                messages=["安全校验失败：允许区域外的像素发生变化。"],
                edits=edits,
            ),
            False,
        )

    _write_image_atomically(working, output_path)
    return (
        EditReport(
            status="success",
            output_path=str(output_path),
            messages=["编辑完成，源图未被覆盖。"],
            edits=edits,
        ),
        True,
    )


def run_pipeline(request: EditRequest, ocr_backend) -> EditReport:
    """Run requested edits without ever overwriting the source image."""
    source_path = Path(request.image_path)
    output_path, report_path, preview_path = _paths(source_path)
    output_published = False
    processing_error: OSError | ValueError | None = None

    try:
        _remove_stale_artifacts(output_path, preview_path)
        with Image.open(source_path) as opened:
            opened.load()
            source = opened.convert("RGB")

        detected = tuple(ocr_backend.detect(source_path))
        decisions = [
            (replacement, choose_candidates(replacement, detected))
            for replacement in request.replacements
        ]
        global_numbers = _global_candidate_numbers(detected)
        decisions, selection_messages = _apply_candidate_numbers(
            decisions, detected
        )
        if any(decision.status != "ready" for _, decision in decisions):
            report = _confirmation_report(
                source,
                decisions,
                global_numbers,
                preview_path,
                selection_messages,
            )
        else:
            report = _conflict_report(
                source, decisions, global_numbers, preview_path
            )
            if report is None:
                report, output_published = _ready_report(
                    source, decisions, output_path
                )
    except (OSError, ValueError) as error:
        output_path.unlink(missing_ok=True)
        processing_error = error
        report = EditReport(status="failed", messages=[f"处理失败：{error}"])

    try:
        _write_report(report_path, report)
    except OSError as report_error:
        if output_published:
            output_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
        if processing_error is not None:
            raise report_error from processing_error
        raise
    return report
