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
    destinations=[]; real_replace=pipeline.os.replace
    def observe(src,dst):
        destinations.append(Path(dst))
        if Path(dst).name.endswith("_edit-report.json"):
            raise OSError("report marker failure")
        return real_replace(src,dst)
    monkeypatch.setattr(pipeline.os,"replace",observe)
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
    request=confirm_request = EditRequest(source,(Replacement("HZ","CS","one",candidate_number=first.edits[0]["candidate_number"],candidate_polygon=tuple(map(tuple,first.edits[0]["polygon"])),candidate_token=first.edits[0]["candidate_token"]),),Path(first.report_path))
    result=run_pipeline(request,SequenceOCR((item(),)))
    assert result.status=="needs_confirmation"
    assert "binding" in " ".join(result.messages).lower() or "run" in " ".join(result.messages).lower()


def test_failed_geometry_report_keeps_source_digest(tmp_path):
    source=chart(tmp_path)
    bad=TextCandidate("HZ",((-1,10),(20,10),(20,24),(-1,24)),.99)
    result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((bad,)))
    assert result.status=="failed"
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
    lock=FileLock(str(source.with_name(f".{source.name}.edit-chart-text.lock")))
    with lock:
        result=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((item(),),(item("CS"),)))
    assert result.status=="failed"
    assert "timed out" in " ".join(result.messages)
    assert Path(result.report_path).exists()
