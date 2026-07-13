"""Transactional OCR-guided chart text replacement."""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

from filelock import FileLock, Timeout
import numpy as np
from PIL import Image, ImageDraw

from .matching import MatchDecision, choose_candidates
from .models import EditReport, EditRequest, Replacement, TextCandidate
from .repair import repair_region
from .render import render_replacement
from .style import candidate_bounds, estimate_style
from .validate import unchanged_outside

EDIT_PADDING = 2
LOCK_TIMEOUT_SECONDS = 30
CandidateEntry = tuple[int, int, Replacement, TextCandidate]


def _paths(source: Path, run_id: str) -> tuple[Path, Path, Path]:
    prefix = f"{source.stem}_{run_id}"
    return (
        source.parent / f"{prefix}_edited.png",
        source.parent / f"{prefix}_edit-report.json",
        source.parent / f"{prefix}_candidates.png",
    )


def _reserve_artifacts(source: Path) -> tuple[str, tuple[Path, Path, Path], set[Path]]:
    """Atomically reserve every formal path, retrying without touching collisions."""
    while True:
        run_id = secrets.token_hex(16)
        paths = _paths(source, run_id)
        created: set[Path] = set()
        try:
            for path in paths:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                created.add(path)
                os.close(descriptor)
            return run_id, paths, created
        except FileExistsError:
            for path in created:
                path.unlink(missing_ok=True)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise


def _discard(paths: set[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporary(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    return Path(name)


def _stage_image(image: Image.Image, destination: Path) -> Path:
    temporary = _temporary(destination)
    try:
        image.save(temporary, format="PNG")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_report(path: Path, report: EditReport) -> None:
    temporary = _temporary(path)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(asdict(report), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_preview(source: Image.Image, entries: list[CandidateEntry], destination: Path) -> None:
    preview = source.convert("RGB").copy()
    draw = ImageDraw.Draw(preview)
    labels: dict[tuple[tuple[int, int], ...], set[int]] = {}
    for number, _, _, candidate in entries:
        labels.setdefault(candidate.polygon, set()).add(number)
    for polygon, numbers in labels.items():
        points = list(polygon)
        if len(points) >= 2:
            draw.line(points + [points[0]], fill=(255, 0, 0), width=2)
            x, y = points[0]
            draw.text((x + 2, max(0, y - 11)), ",".join(map(str, sorted(numbers))),
                      fill=(255, 0, 0), stroke_width=1, stroke_fill="white")
    temporary = _stage_image(preview, destination)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_candidates(candidates, image_size):
    valid: list[TextCandidate] = []
    messages: list[str] = []
    for index, candidate in enumerate(candidates):
        try:
            candidate_bounds(candidate, image_size)
        except (TypeError, ValueError) as error:
            messages.append(f"ignored invalid OCR candidate[{index}]: {error}")
        else:
            valid.append(candidate)
    return tuple(valid), messages


def _global_candidate_numbers(candidates: tuple[TextCandidate, ...]) -> dict[int, int]:
    result: dict[int, int] = {}
    for number, candidate in enumerate(candidates, 1):
        result.setdefault(id(candidate), number)
    return result


def _numbered_entries(decisions, numbers, *, include_ready: bool) -> list[CandidateEntry]:
    return [(numbers[id(candidate)], index, replacement, candidate)
            for index, (replacement, decision) in enumerate(decisions)
            if include_ready or decision.status != "ready"
            for candidate in decision.candidates]


def _polygon_bounds(polygon):
    xs = [p[0] for p in polygon]; ys = [p[1] for p in polygon]
    return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))


def _geometry_match_score(fingerprint, candidate: TextCandidate) -> float | None:
    left, top, right, bottom = _polygon_bounds(fingerprint)
    cl, ct, cr, cb = _polygon_bounds(candidate.polygon)
    width, height, cw, ch = right-left, bottom-top, cr-cl, cb-ct
    if min(width, height, cw, ch) <= 0 or not (0.80 <= cw/width <= 1.25) or not (0.80 <= ch/height <= 1.25):
        return None
    if abs((cl+cr-left-right)/2) > max(3.0, width*.20) or abs((ct+cb-top-bottom)/2) > max(3.0, height*.20):
        return None
    intersection = max(0., min(right,cr)-max(left,cl))*max(0.,min(bottom,cb)-max(top,ct))
    union = width*height + cw*ch - intersection
    score = intersection/union if union else 0.
    return score if score >= .65 else None


def _selection_error(request: EditRequest, digest: str) -> str | None:
    selected = [i for i, replacement in enumerate(request.replacements) if replacement.candidate_number is not None]
    if not selected:
        return None
    if request.confirmation_report_path is None:
        return "candidate selection requires confirmation_report_path"
    try:
        payload = json.loads(request.confirmation_report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return f"confirmation report invalid: {error}"
    if not isinstance(payload, dict) or payload.get("status") != "needs_confirmation" or not payload.get("run_id"):
        return "confirmation report status/run_id is invalid or expired"
    declared_report_path = payload.get("report_path")
    if (not isinstance(declared_report_path, str) or not declared_report_path
            or Path(declared_report_path).resolve() != request.confirmation_report_path.resolve()):
        return "confirmation report path does not match its self-described report path"
    try:
        report_source = Path(payload.get("source_path", "")).resolve()
    except (OSError, ValueError):
        return "confirmation report source path is invalid"
    if report_source != Path(request.image_path).resolve() or payload.get("source_sha256") != digest:
        return "confirmation report source path/digest no longer matches source"
    records = payload.get("edits")
    if not isinstance(records, list):
        return "confirmation report candidate records are invalid"
    for index in selected:
        replacement = request.replacements[index]
        polygon = [list(point) for point in (replacement.candidate_polygon or ())]
        matching = [record for record in records if isinstance(record, dict)
                    and record.get("candidate_token") == replacement.candidate_token]
        if len(matching) != 1:
            return f"replacement[{index}] candidate_token is invalid or expired"
        record = matching[0]
        expected = (record.get("run_id") == payload.get("run_id")
                    and record.get("replacement_index") == index
                    and record.get("old_text") == replacement.old_text
                    and record.get("new_text") == replacement.new_text
                    and record.get("candidate_number") == replacement.candidate_number
                    and record.get("polygon") == polygon)
        if not expected:
            return f"replacement[{index}] token/number/polygon/text binding mismatch"
    return None


def _resolve_selected(decisions):
    resolved=[]; messages=[]
    for index,(replacement,decision) in enumerate(decisions):
        if replacement.candidate_number is None:
            resolved.append((replacement,decision)); continue
        matches=[candidate for candidate in decision.candidates
                 if _geometry_match_score(replacement.candidate_polygon, candidate) is not None]
        if len(matches) != 1:
            messages.append(f"replacement[{index}] candidate geometry matched {len(matches)} current OCR items; reconfirm")
            resolved.append((replacement,MatchDecision("needs_confirmation",decision.candidates)))
        else:
            resolved.append((replacement,MatchDecision("ready",(matches[0],))))
    return resolved,messages


def _candidate_record(number, index, replacement, candidate, run_id):
    return {"run_id": run_id, "candidate_number": number, "candidate_token": secrets.token_urlsafe(24),
            "replacement_index": index, "old_text": replacement.old_text,
            "new_text": replacement.new_text, "text": candidate.text,
            "confidence": candidate.confidence,
            "polygon": [list(point) for point in candidate.polygon]}


def _confirmation(source, decisions, numbers, preview_path, messages, run_id, digest, report_path, source_path, include_ready=False):
    entries=_numbered_entries(decisions,numbers,include_ready=include_ready)
    _publish_preview(source,entries,preview_path)
    return EditReport("needs_confirmation",run_id,digest,report_path=str(report_path),
                      preview_path=str(preview_path),source_path=str(source_path),messages=messages,
                      edits=[_candidate_record(*entry, run_id) for entry in entries])


def _planned(candidate, size):
    left,top,right,bottom=candidate_bounds(candidate,size); width,height=size
    return max(0,left-EDIT_PADDING),max(0,top-EDIT_PADDING),min(width,right+EDIT_PADDING),min(height,bottom+EDIT_PADDING)


def _conflicts(decisions, size):
    chosen=[(i,r,c) for i,(r,d) in enumerate(decisions) for c in d.candidates]
    messages=[]
    for position,left in enumerate(chosen):
        ll,lt,lr,lb=_planned(left[2],size)
        for right in chosen[position+1:]:
            rl,rt,rr,rb=_planned(right[2],size)
            if min(lr,rr)>max(ll,rl) and min(lb,rb)>max(lt,rt):
                messages.append(f"candidate regions conflict: replacement[{left[0]}] and replacement[{right[0]}]")
    return messages


def _inside(candidate: TextCandidate, box, image_size) -> bool:
    left,top,right,bottom=box
    try:
        candidate_bounds(candidate, image_size)
        cl,ct,cr,cb=_polygon_bounds(candidate.polygon)
    except (ValueError, TypeError):
        return False
    cx,cy=(cl+cr)/2,(ct+cb)/2
    return (left <= cx < right and top <= cy < bottom
            and left <= cl and top <= ct and cr <= right and cb <= bottom)


def _post_validate(detected, edits, image_size) -> tuple[bool,list[dict],list[str]]:
    results=[]; messages=[]
    for edit in edits:
        region=[candidate for candidate in detected if candidate.confidence >= .50 and _inside(candidate,edit["allowed_box"],image_size)]
        new=[candidate for candidate in region if candidate.text.strip()==edit["new_text"]]
        old=[candidate for candidate in region if candidate.text.strip()==edit["old_text"]]
        passed=len(new)==1 and not old
        result={"passed":passed,"new_text_matches":len(new),"old_text_matches":len(old),
                "confidence":new[0].confidence if len(new)==1 else None}
        results.append(result)
        if len(new)!=1: messages.append(f"post-OCR new_text validation failed: expected one trusted {edit['new_text']!r}, got {len(new)}")
        if old: messages.append(f"post-OCR old_text validation failed: {edit['old_text']!r} remains")
    return not messages,results,messages


def _ready(source, decisions, output_path, report_path, run_id, digest, source_path, backend):
    working=source.copy(); boxes=[]; edits=[]
    for replacement,decision in decisions:
        for candidate in decision.candidates:
            candidate_bounds(candidate,source.size)
            style=estimate_style(source,candidate)
            working,allowed,method=repair_region(working,candidate,padding=EDIT_PADDING)
            working=render_replacement(working,candidate,replacement.new_text,style,allowed)
            boxes.append(allowed)
            edits.append({"old_text":replacement.old_text,"new_text":replacement.new_text,
                          "confidence":candidate.confidence,"polygon":[list(p) for p in candidate.polygon],
                          "allowed_box":list(allowed),"repair_method":method,"style":asdict(style)})
    before=np.asarray(source); after=np.asarray(working)
    changed=np.any(before != after,axis=tuple(range(2,before.ndim)))
    count=int(changed.sum())
    if count:
        ys,xs=np.where(changed); diff_bbox=[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
    else: diff_bbox=None
    outside_unchanged = unchanged_outside(source, working, boxes)
    mode_preserved = source.mode == working.mode
    alpha_preserved = source.mode != "RGBA" or np.array_equal(before[:, :, 3], after[:, :, 3])
    all_edits_changed = True
    for edit in edits:
        left, top, right, bottom = edit["allowed_box"]
        local = changed[top:bottom, left:right]
        local_count = int(local.sum())
        if local_count:
            ys, xs = np.where(local)
            local_bbox = [int(left+xs.min()), int(top+ys.min()), int(left+xs.max()+1), int(top+ys.max()+1)]
        else:
            local_bbox = None
            all_edits_changed = False
        ring_left, ring_top = max(0, left-1), max(0, top-1)
        ring_right, ring_bottom = min(source.width, right+1), min(source.height, bottom+1)
        ring = changed[ring_top:ring_bottom, ring_left:ring_right].copy()
        ring[top-ring_top:bottom-ring_top, left-ring_left:right-ring_left] = False
        boundary_unchanged = not bool(np.any(ring))
        edit["pixel_diff_count"] = local_count
        edit["pixel_diff_bbox"] = local_bbox
        edit["quality_checks"] = {
            "pixel_change_nonempty": local_count > 0,
            "outside_unchanged": outside_unchanged,
            "mode_preserved": mode_preserved,
            "alpha_preserved": alpha_preserved,
            "outside_boundary_unchanged": boundary_unchanged,
        }
    if count == 0 or not all_edits_changed or not mode_preserved or not alpha_preserved or not outside_unchanged:
        return EditReport("failed",run_id,digest,report_path=str(report_path),source_path=str(source_path),
                          messages=["pixel safety validation failed: empty diff, mode change, or pixels changed outside"],edits=edits),None
    temporary=_stage_image(working,output_path)
    try:
        detected=tuple(backend.detect(temporary))
        passed,results,messages=_post_validate(detected,edits,source.size)
        for edit,result in zip(edits,results): edit["post_ocr_validation"]=result
        if not passed:
            return EditReport("failed",run_id,digest,report_path=str(report_path),source_path=str(source_path),messages=messages,edits=edits),temporary
        os.replace(temporary,output_path)
        return EditReport("success",run_id,digest,output_path=str(output_path),report_path=str(report_path),
                          source_path=str(source_path),messages=["edit complete; source preserved"],edits=edits),None
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _run_locked(request,backend,run_id,output_path,report_path,preview_path):
    source_path=Path(request.image_path).resolve()
    digest=_source_digest(source_path)
    with Image.open(source_path) as opened:
        opened.load(); source=opened.copy()
    selection_error=_selection_error(request,digest)
    if selection_error:
        return EditReport("needs_confirmation",run_id,digest,report_path=str(report_path),source_path=str(source_path),messages=[selection_error]),False,False
    raw_detected=tuple(backend.detect(source_path))
    detected,candidate_messages=_validated_candidates(raw_detected,source.size)
    decisions=[(replacement,choose_candidates(replacement,detected)) for replacement in request.replacements]
    numbers=_global_candidate_numbers(detected)
    decisions,selection_messages=_resolve_selected(decisions)
    if any(decision.status != "ready" for _,decision in decisions):
        messages=candidate_messages+selection_messages+[f"{r.old_text!r}->{r.new_text!r}: {d.status}" for r,d in decisions if d.status!="ready"]
        report=_confirmation(source,decisions,numbers,preview_path,messages,run_id,digest,report_path,source_path)
        return report,False,True
    conflicts=_conflicts(decisions,source.size)
    if conflicts:
        report=_confirmation(source,decisions,numbers,preview_path,candidate_messages+conflicts,run_id,digest,report_path,source_path,include_ready=True)
        return report,False,True
    report,staged=_ready(source,decisions,output_path,report_path,run_id,digest,source_path,backend)
    if candidate_messages:
        report.messages[:0] = candidate_messages
    if staged is not None: staged.unlink(missing_ok=True)
    return report,report.status=="success",False


def run_pipeline(request: EditRequest, ocr_backend) -> EditReport:
    """Run one isolated transaction; the report is its final commit marker."""
    source_path = Path(request.image_path).resolve()
    run_id, paths, owned = _reserve_artifacts(source_path)
    output_path, report_path, preview_path = paths
    keep: set[Path] = set()
    processing_error = None
    lock = FileLock(str(source_path.with_name(f".{source_path.name}.edit-chart-text.lock")), timeout=LOCK_TIMEOUT_SECONDS)
    try:
        try:
            with lock:
                try:
                    report, _, _ = _run_locked(
                        request, ocr_backend, run_id, output_path, report_path, preview_path
                    )
                except (OSError, ValueError) as error:
                    processing_error = error
                    try:
                        failure_digest = _source_digest(source_path)
                    except OSError:
                        failure_digest = ""
                    report = EditReport(
                        "failed", run_id, failure_digest,
                        report_path=str(report_path), source_path=str(source_path),
                        messages=[f"processing failed: {error}"],
                    )
                try:
                    _write_report(report_path, report)
                except OSError as report_error:
                    if processing_error is not None:
                        raise report_error from processing_error
                    raise
        except Timeout:
            report = EditReport(
                "failed", run_id, report_path=str(report_path), source_path=str(source_path),
                messages=[f"processing failed: timed out waiting {LOCK_TIMEOUT_SECONDS}s for source lock"],
            )
            _write_report(report_path, report)

        keep.add(report_path)
        if report.status == "success":
            keep.add(output_path)
        elif report.status == "needs_confirmation" and report.preview_path:
            keep.add(preview_path)
        return report
    finally:
        _discard(owned - keep)
