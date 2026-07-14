import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from filelock import FileLock
import threading
import time

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


def item(text="HZ", x=10, confidence=.99):
    return TextCandidate(text, ((x, 10), (x+20, 10), (x+20, 24), (x, 24)), confidence)


def chart(tmp_path, mode="RGB"):
    path = tmp_path / "chart.png"
    image = Image.new(mode, (80, 40), (255,255,255,137) if mode == "RGBA" else "white")
    draw = ImageDraw.Draw(image)
    fill = (0,0,0,137) if mode == "RGBA" else "black"
    draw.text((10, 10), "HZ", fill=fill)
    draw.text((40, 10), "HZ", fill=fill)
    image.save(path)
    return path


class SequenceOCR:
    def __init__(self, *results): self.results=list(results); self.calls=[]
    def detect(self, path):
        self.calls.append(Path(path))
        return self.results.pop(0)


def test_unique_run_artifacts_do_not_touch_fixed_or_user_files(tmp_path):
    source = chart(tmp_path)
    fixed = [tmp_path/"chart_edited.png", tmp_path/"chart_candidates.png", tmp_path/"chart_edit-report.json"]
    for path in fixed: path.write_bytes(b"user-owned")
    first = run_pipeline(EditRequest(source, (Replacement("HZ","CS","one"),)), SequenceOCR((item(),), (item("CS"),)))
    second = run_pipeline(EditRequest(source, (Replacement("HZ","CS","one"),)), SequenceOCR((item(),), (item("CS"),)))
    assert first.status == second.status == "success"
    assert first.run_id != second.run_id
    assert first.output_path != second.output_path
    assert first.report_path != second.report_path
    assert all(path.read_bytes() == b"user-owned" for path in fixed)
    assert Path(first.output_path).exists() and Path(second.output_path).exists()


def test_invalid_source_does_not_delete_any_artifact(tmp_path):
    user = tmp_path/"missing_edited.png"; user.write_bytes(b"keep")
    report = run_pipeline(EditRequest(tmp_path/"missing.png", (Replacement("HZ","CS","one"),)), SequenceOCR())
    assert report.status == "failed"
    assert user.read_bytes() == b"keep"


@pytest.mark.parametrize("post,fragment", [((item("HZ"),), "old_text"), ((), "new_text")])
def test_post_ocr_failure_does_not_publish_edited_image(tmp_path, post, fragment):
    source=chart(tmp_path)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)), SequenceOCR((item(),), post))
    assert report.status == "failed"
    assert report.output_path is None
    assert fragment in " ".join(report.messages)
    assert not tuple(tmp_path.glob("chart_*_edited.png"))
    payload=json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert payload["status"] == "failed"


def test_substring_missing_complete_post_ocr_target_fails_atomically_with_full_label_audit(tmp_path):
    source=tmp_path/"chart.png"
    image=Image.new("RGB",(240,50),"white")
    ImageDraw.Draw(image).text((10,10),"HZ26-6DPP",fill="black")
    image.save(source)
    original=TextCandidate("HZ26-6DPP（待建）",((10,10),(230,10),(230,30),(10,30)),.99)
    report=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","one",match_mode="substring"),)),
        SequenceOCR((original,),(TextCandidate("CS",original.polygon,.99),)),
    )

    assert report.status=="failed"
    assert report.output_path is None
    assert not tuple(tmp_path.glob("chart_*_edited.png"))
    assert report.edits[0]["source_label"]=="HZ26-6DPP（待建）"
    assert report.edits[0]["target_label"]=="CS26-6DPP（待建）"
    validation=report.edits[0]["post_ocr_validation"]
    assert validation["passed"] is False
    assert validation["source_label"]=="HZ26-6DPP（待建）"
    assert validation["target_label"]=="CS26-6DPP（待建）"
    assert "post-OCR new_text validation failed" in " ".join(report.messages)

def test_post_ocr_success_records_audit_fields_and_rgba_alpha(tmp_path):
    source=chart(tmp_path, "RGBA")
    source_alpha=Image.open(source).getchannel("A").tobytes()
    backend=SequenceOCR((item(),), (item("CS", confidence=.97),))
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)), backend)
    assert report.status == "success"
    assert backend.calls[1] != source
    assert hashlib.sha256(source.read_bytes()).hexdigest() == report.source_sha256
    edit=report.edits[0]
    assert edit["style"] and edit["allowed_box"]
    assert edit["pixel_diff_count"] > 0 and edit["pixel_diff_bbox"]
    assert edit["post_ocr_validation"]["passed"] is True
    assert edit["quality_checks"] == {
        "pixel_change_nonempty": True,
        "outside_unchanged": True,
        "mode_preserved": True,
        "alpha_preserved": True,
        "outside_boundary_unchanged": True,
    }
    with Image.open(report.output_path) as output:
        assert output.mode == "RGBA"
        assert output.getchannel("A").tobytes() == source_alpha


def test_confirmation_token_binds_report_source_replacement_and_polygon(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)), SequenceOCR((item(x=10),item(x=40))))
    chosen=first.edits[1]
    assert chosen["candidate_token"] and len(chosen["candidate_token"]) >= 16
    confirmed=EditRequest(source,(Replacement("HZ","CS","one",candidate_number=chosen["candidate_number"],candidate_polygon=tuple(map(tuple,chosen["polygon"])),candidate_token=chosen["candidate_token"]),),Path(first.report_path))
    second=run_pipeline(confirmed, SequenceOCR((item(x=40),item(x=10)), (item("CS",x=40),)))
    assert second.status == "success", second.messages
    tampered=EditRequest(source,(Replacement("HZ","CS","one",candidate_number=first.edits[0]["candidate_number"],candidate_polygon=tuple(map(tuple,first.edits[0]["polygon"])),candidate_token=chosen["candidate_token"]),),Path(first.report_path))
    rejected=run_pipeline(tampered, SequenceOCR((item(x=10),item(x=40))))
    assert rejected.status == "needs_confirmation"
    assert rejected.output_path is None


def test_substring_confirmation_token_rejects_occurrence_tampering(tmp_path):
    source=chart(tmp_path)
    repeated=item("HZ-HZ")
    first=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((repeated,)),
    )
    chosen=first.edits[0]
    tampered=EditRequest(
        source,
        (Replacement(
            "HZ","CS","one",
            candidate_number=chosen["candidate_number"],
            candidate_polygon=tuple(map(tuple,chosen["polygon"])),
            candidate_token=chosen["candidate_token"],
            match_mode="substring",
            substring_occurrence=2,
        ),),
        Path(first.report_path),
    )

    rejected=run_pipeline(tampered,SequenceOCR((repeated,)))

    assert rejected.status=="needs_confirmation"
    assert rejected.output_path is None
    assert any(
        fragment in " ".join(rejected.messages).lower()
        for fragment in ("binding","authentication")
    )


def test_substring_confirmation_token_accepts_its_bound_occurrence(tmp_path):
    source=chart(tmp_path)
    repeated=item("HZ-HZ")
    first=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((repeated,)),
    )
    chosen=first.edits[1]
    confirmed=EditRequest(
        source,
        (Replacement(
            "HZ","CS","one",
            candidate_number=chosen["candidate_number"],
            candidate_polygon=tuple(map(tuple,chosen["polygon"])),
            candidate_token=chosen["candidate_token"],
            match_mode="substring",
            substring_occurrence=chosen["substring_occurrence"],
        ),),
        Path(first.report_path),
    )

    result=run_pipeline(confirmed,SequenceOCR((repeated,),(item("HZ-CS"),)))

    assert result.status=="success",result.messages


@pytest.mark.parametrize("field",["source_label","target_label"])
def test_substring_confirmation_authenticates_full_label_fields(tmp_path,field):
    source=chart(tmp_path)
    repeated=item("HZ-HZ")
    first=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((repeated,)),
    )
    chosen=first.edits[0]
    payload=json.loads(Path(first.report_path).read_text(encoding="utf-8"))
    payload["edits"][0][field]="tampered-label"
    Path(first.report_path).write_text(json.dumps(payload),encoding="utf-8")
    confirmed=EditRequest(
        source,
        (Replacement(
            "HZ","CS","one",
            candidate_number=chosen["candidate_number"],
            candidate_polygon=tuple(map(tuple,chosen["polygon"])),
            candidate_token=chosen["candidate_token"],
            match_mode="substring",
            substring_occurrence=chosen["substring_occurrence"],
        ),),
        Path(first.report_path),
    )

    result=run_pipeline(confirmed,SequenceOCR((repeated,),(item("CS"),)))

    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert "authentication" in " ".join(result.messages).lower()


def test_confirmation_rejects_source_changed_after_report(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)), SequenceOCR((item(),item(x=40))))
    chosen=first.edits[0]
    Image.new("RGB",(80,40),"black").save(source)
    confirmed=EditRequest(source,(Replacement("HZ","CS","one",candidate_number=chosen["candidate_number"],candidate_polygon=tuple(map(tuple,chosen["polygon"])),candidate_token=chosen["candidate_token"]),),Path(first.report_path))
    result=run_pipeline(confirmed, SequenceOCR((item(),)))
    assert result.status == "needs_confirmation"
    assert "source" in " ".join(result.messages).lower()


def test_report_publish_is_last_marker_and_failure_only_cleans_own_run(tmp_path, monkeypatch):
    source=chart(tmp_path); other=tmp_path/"chart_other_edited.png"; other.write_bytes(b"other")
    destinations=[]; real_link=pipeline.os.link
    def observe(src,dst):
        destinations.append(Path(dst))
        if Path(dst).name.endswith("_edit-report.json"):
            raise OSError("report marker failure")
        return real_link(src,dst)
    monkeypatch.setattr(pipeline.os,"link",observe)
    with pytest.raises(OSError,match="report marker failure"):
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)), SequenceOCR((item(),),(item("CS"),)))
    assert other.read_bytes()==b"other"
    assert destinations[-1].name.endswith("_edit-report.json")
    assert not [path for path in tmp_path.glob("chart_*_edited.png") if path != other]


def test_same_source_runs_are_serialized_and_do_not_cross_paths(tmp_path):
    source=chart(tmp_path); gate=threading.Lock(); active=0; maximum=0
    class SlowOCR:
        def __init__(self): self.n=0
        def detect(self,path):
            nonlocal active,maximum
            with gate: active+=1; maximum=max(maximum,active)
            time.sleep(.05)
            with gate: active-=1
            self.n+=1
            return (item() if self.n==1 else item("CS"),)
    request=EditRequest(source,(Replacement("HZ","CS","one"),))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _: run_pipeline(request,SlowOCR()), range(2)))
    assert maximum == 1
    assert all(r.status=="success" for r in results)
    assert len({r.output_path for r in results}) == 2




def test_confirmation_rejects_report_with_mismatched_run_binding(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((item(),item(x=40))))
    payload=json.loads(Path(first.report_path).read_text(encoding="utf-8"))
    payload["run_id"]="different-run"
    Path(first.report_path).write_text(json.dumps(payload),encoding="utf-8")
    request=EditRequest(source,(Replacement("HZ","CS","one",candidate_number=first.edits[0]["candidate_number"],candidate_polygon=tuple(map(tuple,first.edits[0]["polygon"])),candidate_token=first.edits[0]["candidate_token"]),),Path(first.report_path))
    result=run_pipeline(request,SequenceOCR((item(),)))
    assert result.status=="needs_confirmation"
    assert "authentication" in " ".join(result.messages).lower()


def test_failed_geometry_report_keeps_source_digest(tmp_path):
    source=chart(tmp_path)
    bad=TextCandidate("HZ",((-1,10),(20,10),(20,24),(-1,24)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((bad,)))
    assert result.status=="needs_confirmation"
    assert result.edits == []
    assert result.source_sha256==hashlib.sha256(source.read_bytes()).hexdigest()


def test_post_ocr_rejects_out_of_image_verification_polygon(tmp_path):
    source=chart(tmp_path)
    bad_new=TextCandidate("CS",((-1,10),(20,10),(20,24),(-1,24)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(bad_new,)))
    assert result.status=="failed"
    assert result.edits[0]["post_ocr_validation"]["passed"] is False


def test_post_ocr_rejects_implausibly_large_geometry(tmp_path):
    source=chart(tmp_path)
    giant=TextCandidate("CS",((0,0),(40,0),(40,34),(0,34)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(giant,)))
    assert result.status=="failed"
    assert result.edits[0]["post_ocr_validation"]["passed"] is False



def test_each_edit_requires_nonempty_pixel_diff(tmp_path):
    source=tmp_path/"chart.png"
    image=Image.new("RGB",(90,40),"white")
    ImageDraw.Draw(image).text((10,10),"HZ",fill="black")
    image.save(source)
    blank=TextCandidate("P10",((50,10),(70,10),(70,24),(50,24)),.99)
    request=EditRequest(source,(Replacement("HZ","CS","one"),Replacement("P10","P20","one")))
    result=run_pipeline(request,SequenceOCR((item(),blank),(item("CS"),TextCandidate("P20",blank.polygon,.99))))
    assert result.status=="failed"
    assert result.edits[1]["pixel_diff_count"]==0


def test_lock_timeout_returns_clear_failed_report(tmp_path, monkeypatch):
    source=chart(tmp_path)
    monkeypatch.setattr(pipeline,"LOCK_TIMEOUT_SECONDS",.01)
    state=pipeline._state_dir()
    identity=pipeline._prelock_identity(source)
    lock=FileLock(str(pipeline._source_lock_path(source,identity,state)))
    with lock:
        result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(item("CS"),)))
    assert result.status=="failed"
    assert "timed out" in " ".join(result.messages)
    assert Path(result.report_path).exists()


def test_confirmation_report_rejects_copied_or_renamed_report(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((item(),item(x=40))))
    copied=tmp_path/"copied-report.json"
    copied.write_bytes(Path(first.report_path).read_bytes())
    chosen=first.edits[0]
    request=EditRequest(source,(Replacement("HZ","CS","one",candidate_number=chosen["candidate_number"],candidate_polygon=tuple(map(tuple,chosen["polygon"])),candidate_token=chosen["candidate_token"]),),copied)
    result=run_pipeline(request,SequenceOCR((item(),item(x=40)),(item("CS"),)))
    assert result.status=="needs_confirmation"
    assert "report path" in " ".join(result.messages).lower()


def test_predictable_run_id_collision_never_overwrites_existing_artifact(tmp_path, monkeypatch):
    source=chart(tmp_path)
    collided="a"*32; fresh="b"*32
    existing=tmp_path/f"chart_{collided}_edited.png"
    existing.write_bytes(b"user-owned")
    ids=iter((collided,fresh))
    monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: next(ids))
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(item("CS"),)))
    assert result.status=="failed"
    assert result.run_id==collided
    assert existing.read_bytes()==b"user-owned"
    assert Path(result.report_path).exists()


def test_mid_reservation_collision_cleans_only_attempt_placeholders(tmp_path, monkeypatch):
    source=chart(tmp_path)
    collided="c"*32; fresh="d"*32
    existing_report=tmp_path/f"chart_{collided}_edit-report.json"
    existing_report.write_bytes(b"user-report")
    ids=iter((collided,fresh))
    monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: next(ids))
    with pytest.raises(OSError):
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(item("CS"),)))
    assert existing_report.read_bytes()==b"user-report"
    assert not (tmp_path/f"chart_{collided}_edited.png").exists()


def test_invalid_detected_candidates_never_receive_tokens_or_enter_report(tmp_path):
    source=chart(tmp_path)
    valid=(item(x=10),item(x=40))
    outside=TextCandidate("HZ",((-1,10),(20,10),(20,24),(-1,24)),.99)
    degenerate=TextCandidate("HZ",((5,5),(10,5),(15,5),(20,5)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR(valid+(outside,degenerate)))
    assert result.status=="needs_confirmation"
    assert len(result.edits)==2
    assert {tuple(map(tuple,edit["polygon"])) for edit in result.edits}=={candidate.polygon for candidate in valid}
    assert "invalid" in " ".join(result.messages).lower()


def test_only_invalid_detected_candidate_becomes_safe_not_found_confirmation(tmp_path):
    source=chart(tmp_path)
    invalid=TextCandidate("HZ",((10,10),(20,10),(30,10),(40,10)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((invalid,)))
    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert result.edits==[]
    assert "invalid" in " ".join(result.messages).lower()
