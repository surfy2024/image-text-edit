"""Transactional OCR-guided chart text replacement."""

from dataclasses import asdict, dataclass
import errno
import hashlib
import hmac
import json
from io import BytesIO
import os
from pathlib import Path
import secrets
import tempfile

from filelock import FileLock, Timeout
import numpy as np
from PIL import Image, ImageDraw

from .matching import (
    MatchDecision,
    choose_candidates,
    derive_target_label,
    substring_occurrences,
)
from .models import EditReport, EditRequest, Replacement, TextCandidate
from .repair import repair_region
from .render import render_replacement
from .style import candidate_bounds, estimate_style
from .validate import unchanged_outside

EDIT_PADDING = 2
LOCK_TIMEOUT_SECONDS = 30
CandidateEntry = tuple[int, int, Replacement, TextCandidate, int | None]


class ArtifactPublishError(RuntimeError):
    """A formal artifact cannot be published with no-replace semantics."""


def _paths(source: Path, run_id: str) -> tuple[Path, Path, Path]:
    prefix = f"{source.stem}_{run_id}"
    return (
        source.parent / f"{prefix}_edited.png",
        source.parent / f"{prefix}_edit-report.json",
        source.parent / f"{prefix}_candidates.png",
    )


def _state_dir() -> Path:
    configured = os.environ.get("EDIT_CHART_TEXT_STATE_DIR")
    state = Path(configured).expanduser() if configured else Path.home() / ".codex" / "state" / "edit-chart-text"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(state, 0o700)
    except OSError:
        pass
    return state.resolve()


def _stat_fields(value) -> dict[str, int]:
    return {
        "dev": int(value.st_dev),
        "ino": int(value.st_ino),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
    }


def _path_identity(source: Path) -> dict:
    return {
        "lexical_path": os.path.normcase(os.path.abspath(str(source))),
        "resolved_path": os.path.normcase(str(source.resolve(strict=True))),
        "lstat": _stat_fields(source.lstat()),
        "target_stat": _stat_fields(source.stat()),
    }


def _prelock_identity(source: Path) -> dict:
    try:
        return _path_identity(source)
    except OSError:
        return {
            "lexical_path": os.path.normcase(os.path.abspath(str(source))),
            "unavailable": True,
        }


def _source_lock_path(source: Path, identity: dict, state: Path) -> Path:
    payload = json.dumps(
        {"source": os.path.normcase(os.path.abspath(str(source))), "identity": identity},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return state / f"source-{hashlib.sha256(payload).hexdigest()}.lock"


def _load_or_create_secret(state: Path | None = None) -> bytes:
    state = _state_dir() if state is None else Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_path = state / "install-secret.bin"
    with FileLock(str(state / "install-secret.lock"), timeout=LOCK_TIMEOUT_SECONDS):
        if secret_path.exists():
            return _read_secret(secret_path)
        secret = secrets.token_bytes(32)
        descriptor, name = tempfile.mkstemp(
            prefix=".install-secret-", suffix=".tmp", dir=state
        )
        temporary = Path(name)
        try:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            try:
                offset = 0
                while offset < len(secret):
                    written = os.write(descriptor, secret[offset:])
                    if written <= 0:
                        raise OSError("install secret write made no progress")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                _publish_no_replace(temporary, secret_path)
            except FileExistsError:
                return _read_secret(secret_path)
            try:
                os.chmod(secret_path, 0o600)
            except OSError:
                pass
            return secret
        finally:
            _best_effort_unlink(temporary)


def _read_secret(path: Path) -> bytes:
    secret = path.read_bytes()
    if len(secret) != 32:
        raise ValueError("install secret must contain exactly 32 bytes")
    return secret


def _reserve_run(source: Path) -> tuple[str, Path]:
    while True:
        run_id = secrets.token_hex(16)
        marker = source.parent / f".{source.stem}_{run_id}.edit-chart-text.reserve"
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        try:
            os.write(descriptor, run_id.encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return run_id, marker


@dataclass(frozen=True)
class _SourceCapture:
    data: bytes
    digest: str
    identity: dict


def _capture_source(source: Path) -> _SourceCapture:
    before = _path_identity(source)
    with source.open("rb") as stream:
        descriptor_before = _stat_fields(os.fstat(stream.fileno()))
        data = stream.read()
        descriptor_after = _stat_fields(os.fstat(stream.fileno()))
    after = _path_identity(source)
    if descriptor_before != descriptor_after or before != after or descriptor_after != after["target_stat"]:
        raise ValueError("source changed while creating immutable snapshot")
    if len(data) != descriptor_after["size"]:
        raise ValueError("source changed while reading immutable snapshot")
    return _SourceCapture(data, hashlib.sha256(data).hexdigest(), after)


def _verify_source(source: Path, capture: _SourceCapture) -> None:
    try:
        current = _capture_source(source)
    except (OSError, ValueError) as error:
        raise ValueError(f"source changed after snapshot: {error}") from error
    if current.digest != capture.digest or current.identity != capture.identity:
        raise ValueError("source changed after snapshot: path identity or digest mismatch")


def _write_snapshot(source: Path, run_id: str, data: bytes) -> Path:
    suffix = source.suffix or ".img"
    descriptor, name = tempfile.mkstemp(
        prefix=f".{source.stem}_{run_id}_snapshot-", suffix=suffix, dir=source.parent
    )
    snapshot = Path(name)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    return snapshot


def _platform_name() -> str:
    return os.name


def _hardlink_is_unsupported(error: OSError) -> bool:
    unsupported_errnos = {
        code for code in (
            errno.EXDEV,
            getattr(errno, "ENOSYS", None),
            getattr(errno, "ENOTSUP", None),
            getattr(errno, "EOPNOTSUPP", None),
        ) if code is not None
    }
    return (
        error.errno in unsupported_errnos
        or getattr(error, "winerror", None) in {1, 17, 50}
    )


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError as error:
        raise FileExistsError(f"formal artifact already exists: {destination}") from error
    except OSError as error:
        if not _hardlink_is_unsupported(error):
            raise
        if _platform_name() != "nt":
            raise ArtifactPublishError(
                "hardlink publish is unsupported; copy the source to a hardlink-capable "
                "local volume. The no-replace rename fallback is Windows-only."
            ) from error
        try:
            os.rename(temporary, destination)
        except FileExistsError as collision:
            raise FileExistsError(
                f"formal artifact already exists: {destination}"
            ) from collision
        except OSError as collision:
            if destination.exists():
                raise FileExistsError(
                    f"formal artifact already exists: {destination}"
                ) from collision
            raise
        return
    _best_effort_unlink(temporary)


def _discard(paths) -> None:
    for path in paths:
        _best_effort_unlink(path)

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
        _publish_no_replace(temporary, path)
    finally:
        _best_effort_unlink(temporary)


def _stage_preview(source: Image.Image, entries: list[CandidateEntry], destination: Path) -> Path:
    preview = source.convert("RGB").copy()
    draw = ImageDraw.Draw(preview)
    labels: dict[tuple[tuple[int, int], ...], set[int]] = {}
    for number, _, _, candidate, _ in entries:
        labels.setdefault(candidate.polygon, set()).add(number)
    for polygon, numbers in labels.items():
        points = list(polygon)
        if len(points) >= 2:
            draw.line(points + [points[0]], fill=(255, 0, 0), width=2)
            x, y = points[0]
            draw.text((x + 2, max(0, y - 11)), ",".join(map(str, sorted(numbers))),
                      fill=(255, 0, 0), stroke_width=1, stroke_fill="white")
    return _stage_image(preview, destination)


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
    entries: list[CandidateEntry] = []
    for index, (replacement, decision) in enumerate(decisions):
        if not include_ready and decision.status == "ready":
            continue
        for candidate in decision.candidates:
            if replacement.match_mode == "substring":
                occurrences = range(1, len(substring_occurrences(
                    candidate.text, replacement.old_text
                )) + 1)
            else:
                occurrences = (None,)
            entries.extend(
                (numbers[id(candidate)], index, replacement, candidate, occurrence)
                for occurrence in occurrences
            )
    return entries


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


def _candidate_payload(
    *, report_path, run_id, source_path, source_sha256, source_identity,
    replacement_index, old_text, new_text, match_mode, source_label, target_label,
    substring_occurrence, candidate_number, polygon,
) -> dict:
    return {
        "report_path": str(Path(report_path).resolve()),
        "run_id": run_id,
        "source_path": str(Path(source_path).absolute()),
        "source_sha256": source_sha256,
        "source_identity": source_identity,
        "replacement_index": replacement_index,
        "old_text": old_text,
        "new_text": new_text,
        "match_mode": match_mode,
        "source_label": source_label,
        "target_label": target_label,
        "substring_occurrence": substring_occurrence,
        "candidate_number": candidate_number,
        "polygon": polygon,
    }


def _canonical_payload(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sign_candidate(secret: bytes, payload: dict) -> str:
    nonce = secrets.token_urlsafe(24)
    signature = hmac.new(
        secret, nonce.encode("ascii") + b"." + _canonical_payload(payload), hashlib.sha256
    ).hexdigest()
    return f"v1.{nonce}.{signature}"


def _verify_candidate_token(secret: bytes, token: str, payload: dict) -> bool:
    try:
        version, nonce, supplied = token.split(".", 2)
    except (AttributeError, ValueError):
        return False
    if version != "v1" or not nonce or len(supplied) != 64:
        return False
    expected = hmac.new(
        secret, nonce.encode("ascii") + b"." + _canonical_payload(payload), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


def _selection_error(
    request: EditRequest, digest: str, identity: dict, secret: bytes
) -> str | None:
    selected = [
        index for index, replacement in enumerate(request.replacements)
        if replacement.candidate_number is not None
    ]
    if not selected:
        return None
    if request.confirmation_report_path is None:
        return "candidate selection requires confirmation_report_path"
    try:
        payload = json.loads(request.confirmation_report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"confirmation report invalid: {error}"
    if not isinstance(payload, dict) or payload.get("status") != "needs_confirmation" or not payload.get("run_id"):
        return "confirmation report status/run_id is invalid or expired"
    declared_report_path = payload.get("report_path")
    if (not isinstance(declared_report_path, str) or not declared_report_path
            or Path(declared_report_path).resolve() != request.confirmation_report_path.resolve()):
        return "confirmation report path does not match its self-described report path"
    try:
        report_source = Path(payload.get("source_path", "")).absolute()
    except (OSError, ValueError):
        return "confirmation report source path is invalid"
    if (report_source != Path(request.image_path).absolute()
            or payload.get("source_sha256") != digest
            or payload.get("source_identity") != identity):
        return "confirmation report source path/digest/identity no longer matches source"
    records = payload.get("edits")
    if not isinstance(records, list):
        return "confirmation report candidate records are invalid"
    for index in selected:
        replacement = request.replacements[index]
        polygon = [list(point) for point in (replacement.candidate_polygon or ())]
        matching = [
            record for record in records if isinstance(record, dict)
            and isinstance(record.get("candidate_token"), str)
            and hmac.compare_digest(record["candidate_token"], replacement.candidate_token or "")
        ]
        if len(matching) != 1:
            return f"replacement[{index}] candidate token is invalid or expired"
        record = matching[0]
        token_payload = _candidate_payload(
            report_path=declared_report_path,
            run_id=payload.get("run_id"),
            source_path=payload.get("source_path"),
            source_sha256=payload.get("source_sha256"),
            source_identity=payload.get("source_identity"),
            replacement_index=record.get("replacement_index"),
            old_text=record.get("old_text"),
            new_text=record.get("new_text"),
            match_mode=record.get("match_mode"),
            source_label=record.get("source_label"),
            target_label=record.get("target_label"),
            substring_occurrence=record.get("substring_occurrence"),
            candidate_number=record.get("candidate_number"),
            polygon=record.get("polygon"),
        )
        if record.get("match_mode") == "substring" and (
            not isinstance(record.get("source_label"), str)
            or not record["source_label"]
            or not isinstance(record.get("target_label"), str)
            or not record["target_label"]
        ):
            return f"replacement[{index}] candidate token authentication failed"
        if not _verify_candidate_token(secret, record["candidate_token"], token_payload):
            return f"replacement[{index}] candidate token authentication failed"
        expected = (
            record.get("run_id") == payload.get("run_id")
            and record.get("replacement_index") == index
            and record.get("old_text") == replacement.old_text
            and record.get("new_text") == replacement.new_text
            and record.get("match_mode") == replacement.match_mode
            and record.get("substring_occurrence") == replacement.substring_occurrence
            and record.get("candidate_number") == replacement.candidate_number
            and record.get("polygon") == polygon
        )
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
            try:
                derive_target_label(replacement, matches[0])
            except ValueError as error:
                messages.append(
                    f"replacement[{index}] substring occurrence is no longer valid; "
                    f"reconfirm: {error}"
                )
                resolved.append((
                    replacement, MatchDecision("needs_confirmation", decision.candidates)
                ))
            else:
                resolved.append((replacement,MatchDecision("ready",(matches[0],))))
    return resolved,messages


def _candidate_record(
    number, index, replacement, candidate, occurrence, *, run_id, digest, identity,
    report_path, source_path, secret,
):
    polygon = [list(point) for point in candidate.polygon]
    source_label, target_label, occurrence = derive_target_label(
        replacement, candidate, occurrence
    )
    payload = _candidate_payload(
        report_path=report_path, run_id=run_id, source_path=source_path,
        source_sha256=digest, source_identity=identity,
        replacement_index=index, old_text=replacement.old_text,
        new_text=replacement.new_text, match_mode=replacement.match_mode,
        source_label=source_label, target_label=target_label,
        substring_occurrence=occurrence, candidate_number=number, polygon=polygon,
    )
    return {
        "run_id": run_id,
        "candidate_number": number,
        "candidate_token": _sign_candidate(secret, payload),
        "replacement_index": index,
        "old_text": replacement.old_text,
        "new_text": replacement.new_text,
        "match_mode": replacement.match_mode,
        "source_label": source_label,
        "target_label": target_label,
        "substring_occurrence": occurrence,
        "text": candidate.text,
        "confidence": candidate.confidence,
        "polygon": polygon,
    }


def _confirmation(
    source, decisions, numbers, preview_path, messages, run_id, digest, identity,
    report_path, source_path, secret, include_ready=False,
):
    entries = _numbered_entries(decisions, numbers, include_ready=include_ready)
    preview_temporary = _stage_preview(source, entries, preview_path)
    report = EditReport(
        "needs_confirmation", run_id, digest,
        report_path=str(report_path), preview_path=str(preview_path),
        source_path=str(source_path), source_identity=identity, messages=messages,
        edits=[
            _candidate_record(
                *entry, run_id=run_id, digest=digest, identity=identity,
                report_path=report_path, source_path=source_path, secret=secret,
            )
            for entry in entries
        ],
    )
    return report, preview_temporary

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


def _intersection_fraction(box, allowed) -> float:
    left, top, right, bottom = box
    al, at, ar, ab = allowed
    area = max(0.0, right-left) * max(0.0, bottom-top)
    if area <= 0:
        return 0.0
    intersection = max(0.0, min(right, ar)-max(left, al)) * max(
        0.0, min(bottom, ab)-max(top, at)
    )
    return intersection / area


def _post_geometry_match(candidate: TextCandidate, edit: dict, image_size) -> bool:
    try:
        candidate_bounds(candidate, image_size)
        candidate_box = _polygon_bounds(candidate.polygon)
        original_box = _polygon_bounds(tuple(tuple(point) for point in edit["polygon"]))
        allowed = tuple(edit["allowed_box"])
    except (KeyError, TypeError, ValueError):
        return False
    left, top, right, bottom = candidate_box
    original_left, original_top, original_right, original_bottom = original_box
    original_width = original_right-original_left
    original_height = original_bottom-original_top
    width = right-left
    height = bottom-top
    if min(original_width, original_height, width, height) <= 0:
        return False
    vertical_center_delta = abs((top+bottom-original_top-original_bottom)/2)
    height_ratio = height/original_height
    left_anchor_delta = abs(left-original_left)
    center_delta = abs((left+right-original_left-original_right)/2)
    return (
        0.65 <= height_ratio <= 1.25
        and vertical_center_delta <= max(2.0, original_height*0.30)
        and left_anchor_delta <= max(4.0, original_width*0.20)
        and center_delta <= max(8.0, original_width*0.60)
        and _intersection_fraction(candidate_box, allowed) >= 0.80
    )


def _post_validate(detected, edits, image_size) -> tuple[bool,list[dict],list[str]]:
    results=[]; messages=[]
    for edit in edits:
        region=[
            candidate for candidate in detected
            if candidate.confidence >= .50 and _post_geometry_match(candidate,edit,image_size)
        ]
        target_label=edit["target_label"]
        source_label=edit["source_label"]
        new=[candidate for candidate in region if candidate.text.strip()==target_label]
        old=[candidate for candidate in region if candidate.text.strip()==source_label]
        passed=len(new)==1 and not old
        result={"passed":passed,"new_text_matches":len(new),"old_text_matches":len(old),
                "confidence":new[0].confidence if len(new)==1 else None,
                "source_label":source_label,"target_label":target_label}
        results.append(result)
        if len(new)!=1: messages.append(f"post-OCR new_text validation failed: expected one trusted {target_label!r}, got {len(new)}")
        if old: messages.append(f"post-OCR old_text validation failed: {source_label!r} remains")
    return not messages,results,messages

def _ready(source, decisions, output_path, report_path, run_id, digest, identity, source_path, backend):
    working=source.copy(); boxes=[]; edits=[]
    for replacement,decision in decisions:
        for candidate in decision.candidates:
            candidate_bounds(candidate,source.size)
            source_label,target_label,occurrence=derive_target_label(replacement,candidate)
            style=estimate_style(source,candidate)
            working,allowed,method=repair_region(working,candidate,padding=EDIT_PADDING)
            working=render_replacement(working,candidate,target_label,style,allowed)
            boxes.append(allowed)
            edits.append({"old_text":replacement.old_text,"new_text":replacement.new_text,
                          "match_mode":replacement.match_mode,"source_label":source_label,
                          "target_label":target_label,"substring_occurrence":occurrence,
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
                          source_identity=identity, messages=["pixel safety validation failed: empty diff, mode change, or pixels changed outside"],edits=edits),None
    temporary=_stage_image(working,output_path)
    try:
        detected=tuple(backend.detect(temporary))
        passed,results,messages=_post_validate(detected,edits,source.size)
        for edit,result in zip(edits,results): edit["post_ocr_validation"]=result
        if not passed:
            return EditReport("failed",run_id,digest,report_path=str(report_path),source_path=str(source_path),source_identity=identity,messages=messages,edits=edits),temporary
        return EditReport("success",run_id,digest,output_path=str(output_path),report_path=str(report_path),
                          source_path=str(source_path),source_identity=identity,
                          messages=["edit complete; source preserved"],edits=edits),temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass
class _RunWork:
    report: EditReport
    output_temporary: Path | None = None
    preview_temporary: Path | None = None


def _run_locked(
    request, backend, run_id, output_path, report_path, preview_path,
    source_path, capture, secret,
) -> _RunWork:
    with Image.open(BytesIO(capture.data)) as opened:
        opened.load()
        source = opened.copy()
    selection_error = _selection_error(
        request, capture.digest, capture.identity, secret
    )
    if selection_error:
        return _RunWork(EditReport(
            "needs_confirmation", run_id, capture.digest,
            report_path=str(report_path), source_path=str(source_path),
            source_identity=capture.identity, messages=[selection_error],
        ))

    snapshot = _write_snapshot(source_path, run_id, capture.data)
    try:
        raw_detected = tuple(backend.detect(snapshot))
    finally:
        snapshot.unlink(missing_ok=True)
    detected, candidate_messages = _validated_candidates(raw_detected, source.size)
    decisions = [
        (replacement, choose_candidates(replacement, detected))
        for replacement in request.replacements
    ]
    numbers = _global_candidate_numbers(detected)
    decisions, selection_messages = _resolve_selected(decisions)
    if any(decision.status != "ready" for _, decision in decisions):
        messages = candidate_messages + selection_messages + [
            f"{replacement.old_text!r}->{replacement.new_text!r}: {decision.status}"
            for replacement, decision in decisions if decision.status != "ready"
        ]
        report, preview_temporary = _confirmation(
            source, decisions, numbers, preview_path, messages, run_id,
            capture.digest, capture.identity, report_path, source_path, secret,
        )
        return _RunWork(report, preview_temporary=preview_temporary)

    conflicts = _conflicts(decisions, source.size)
    if conflicts:
        report, preview_temporary = _confirmation(
            source, decisions, numbers, preview_path,
            candidate_messages + conflicts, run_id, capture.digest,
            capture.identity, report_path, source_path, secret,
            include_ready=True,
        )
        return _RunWork(report, preview_temporary=preview_temporary)

    report, output_temporary = _ready(
        source, decisions, output_path, report_path, run_id, capture.digest,
        capture.identity, source_path, backend,
    )
    if candidate_messages:
        report.messages[:0] = candidate_messages
    return _RunWork(report, output_temporary=output_temporary)


def _source_changed_report(
    run_id: str, report_path: Path, source_path: Path,
    capture: _SourceCapture | None, message: str,
) -> EditReport:
    return EditReport(
        "failed", run_id, capture.digest if capture else "",
        report_path=str(report_path), source_path=str(source_path),
        source_identity=capture.identity if capture else {},
        messages=[f"source changed; no edited artifact committed: {message}"],
    )


def run_pipeline(request: EditRequest, ocr_backend) -> EditReport:
    """Run one isolated transaction; the report is its final commit marker."""
    source_path = Path(os.path.abspath(str(request.image_path)))
    run_id, marker = _reserve_run(source_path)
    output_path, report_path, preview_path = _paths(source_path, run_id)
    state = _state_dir()
    preliminary_identity = _prelock_identity(source_path)
    lock = FileLock(
        str(_source_lock_path(source_path, preliminary_identity, state)),
        timeout=LOCK_TIMEOUT_SECONDS,
    )
    temporary_paths: set[Path] = set()
    published_paths: set[Path] = set()
    committed = False
    capture: _SourceCapture | None = None
    processing_error: BaseException | None = None
    try:
        try:
            with lock:
                try:
                    capture = _capture_source(source_path)
                    if (not preliminary_identity.get("unavailable")
                            and capture.identity != preliminary_identity):
                        raise ValueError("source changed before immutable snapshot was captured")
                    secret = _load_or_create_secret(state)
                    work = _run_locked(
                        request, ocr_backend, run_id, output_path, report_path,
                        preview_path, source_path, capture, secret,
                    )
                    if work.output_temporary:
                        temporary_paths.add(work.output_temporary)
                    if work.preview_temporary:
                        temporary_paths.add(work.preview_temporary)
                    _verify_source(source_path, capture)
                    report = work.report
                except (OSError, ValueError) as error:
                    processing_error = error
                    if capture is not None and "source changed" in str(error).lower():
                        report = _source_changed_report(
                            run_id, report_path, source_path, capture, str(error)
                        )
                    else:
                        report = EditReport(
                            "failed", run_id, capture.digest if capture else "",
                            report_path=str(report_path), source_path=str(source_path),
                            source_identity=capture.identity if capture else {},
                            messages=[f"processing failed: {error}"],
                        )

                if report.status == "success" and work.output_temporary:
                    try:
                        _publish_no_replace(work.output_temporary, output_path)
                        published_paths.add(output_path)
                    except FileExistsError as error:
                        report = EditReport(
                            "failed", run_id, capture.digest if capture else "",
                            report_path=str(report_path), source_path=str(source_path),
                            source_identity=capture.identity if capture else {},
                            messages=[f"processing failed: {error}"],
                        )
                elif report.status == "needs_confirmation" and work.preview_temporary:
                    try:
                        _publish_no_replace(work.preview_temporary, preview_path)
                        published_paths.add(preview_path)
                    except FileExistsError as error:
                        report = EditReport(
                            "failed", run_id, capture.digest if capture else "",
                            report_path=str(report_path), source_path=str(source_path),
                            source_identity=capture.identity if capture else {},
                            messages=[f"processing failed: {error}"],
                        )

                if capture is not None:
                    try:
                        _verify_source(source_path, capture)
                    except ValueError as error:
                        _discard(published_paths)
                        published_paths.clear()
                        report = _source_changed_report(
                            run_id, report_path, source_path, capture, str(error)
                        )
                try:
                    _write_report(report_path, report)
                except OSError as report_error:
                    if processing_error is not None:
                        raise report_error from processing_error
                    raise
        except Timeout:
            report = EditReport(
                "failed", run_id, report_path=str(report_path),
                source_path=str(source_path),
                messages=[f"processing failed: timed out waiting {LOCK_TIMEOUT_SECONDS}s for source lock"],
            )
            _write_report(report_path, report)

        committed = True
        return report
    finally:
        marker.unlink(missing_ok=True)
        _discard(temporary_paths)
        if not committed:
            _discard(published_paths)
