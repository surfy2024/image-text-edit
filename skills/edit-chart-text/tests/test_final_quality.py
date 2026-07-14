import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


def candidate(text="HZ", polygon=None, confidence=.99):
    return TextCandidate(text, polygon or ((10,10),(30,10),(30,24),(10,24)), confidence)


def chart(tmp_path, name="chart.png", color="white"):
    path=tmp_path/name
    image=Image.new("RGB",(80,40),color)
    ImageDraw.Draw(image).text((10,10),"HZ",fill="black")
    image.save(path)
    return path


class SequenceOCR:
    def __init__(self,*results): self.results=list(results); self.paths=[]
    def detect(self,path): self.paths.append(Path(path)); return self.results.pop(0)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state=tmp_path/"app-state"
    monkeypatch.setenv("EDIT_CHART_TEXT_STATE_DIR",str(state))
    return state


def request(source,new="CS"):
    return EditRequest(source,(Replacement("HZ",new,"one"),))


def confirmed_request(source,report,index=0,new="CS"):
    record=report.edits[index]
    return EditRequest(source,(Replacement("HZ",new,"one",candidate_number=record["candidate_number"],candidate_polygon=tuple(map(tuple,record["polygon"])),candidate_token=record["candidate_token"]),),Path(report.report_path))


def test_source_adjacent_lock_file_is_never_touched(tmp_path):
    source=chart(tmp_path)
    adjacent=tmp_path/".chart.png.edit-chart-text.lock"
    adjacent.write_bytes(b"user-owned-lock-data")
    class LockInspectingOCR:
        def __init__(self): self.calls=0
        def detect(self,path):
            self.calls+=1
            assert tuple((tmp_path/"app-state").glob("source-*.lock"))
            return (candidate(),) if self.calls==1 else (candidate("CS"),)
    result=run_pipeline(request(source),LockInspectingOCR())
    assert result.status=="success"
    assert adjacent.read_bytes()==b"user-owned-lock-data"


def test_initial_ocr_reads_immutable_same_extension_snapshot(tmp_path):
    source=chart(tmp_path); original=source.read_bytes()
    class InspectingOCR:
        def __init__(self): self.calls=0
        def detect(self,path):
            self.calls+=1; path=Path(path)
            if self.calls==1:
                assert path != source
                assert path.suffix==source.suffix
                assert path.read_bytes()==original
                return (candidate(),)
            return (candidate("CS"),)
    result=run_pipeline(request(source),InspectingOCR())
    assert result.status=="success"
    assert result.source_identity


def test_source_replaced_during_initial_ocr_fails_without_edited_commit(tmp_path):
    source=chart(tmp_path)
    class MutatingOCR:
        def detect(self,path):
            Image.new("RGB",(80,40),"red").save(source)
            return (candidate(),)
    result=run_pipeline(request(source),MutatingOCR())
    assert result.status=="failed"
    assert result.output_path is None
    assert not tuple(tmp_path.glob("chart_*_edited.png"))
    assert "source changed" in " ".join(result.messages).lower()


def test_source_replaced_during_post_ocr_fails_without_edited_commit(tmp_path):
    source=chart(tmp_path)
    class MutatingPostOCR:
        def __init__(self): self.calls=0
        def detect(self,path):
            self.calls+=1
            if self.calls==1: return (candidate(),)
            Image.new("RGB",(80,40),"blue").save(source)
            return (candidate("CS"),)
    result=run_pipeline(request(source),MutatingPostOCR())
    assert result.status=="failed"
    assert result.output_path is None
    assert not tuple(tmp_path.glob("chart_*_edited.png"))


def test_symlink_target_swap_is_detected_when_supported(tmp_path):
    first=chart(tmp_path,"first.png"); second=chart(tmp_path,"second.png",color="yellow")
    link=tmp_path/"chart-link.png"
    try: os.symlink(first.name,link)
    except OSError as error: pytest.skip(f"symlink unavailable: {error}")
    class SwapOCR:
        def detect(self,path):
            link.unlink(); os.symlink(second.name,link)
            return (candidate(),)
    result=run_pipeline(request(link),SwapOCR())
    assert result.status=="failed"
    assert result.output_path is None


def _edit_geometry():
    return {"old_text":"HZ","new_text":"CS","source_label":"HZ","target_label":"CS","polygon":[[10,10],[30,10],[30,24],[10,24]],"allowed_box":[8,8,32,26]}


def test_post_ocr_neighbor_at_allowed_corner_does_not_validate():
    neighbor=candidate("CS",((8,8),(14,8),(14,13),(8,13)))
    passed,results,_=pipeline._post_validate((neighbor,),(_edit_geometry(),),(80,40))
    assert passed is False and results[0]["new_text_matches"]==0


def test_post_ocr_short_left_aligned_replacement_validates():
    correct=candidate("CS",((10,11),(22,11),(22,23),(10,23)))
    passed,results,_=pipeline._post_validate((correct,),(_edit_geometry(),),(80,40))
    assert passed is True and results[0]["passed"] is True


def test_post_ocr_allows_small_detector_vertical_expansion():
    correct=candidate("CS",((10,7),(22,7),(22,20),(10,20)))
    passed,results,_=pipeline._post_validate((correct,),(_edit_geometry(),),(80,40))
    assert passed is True and results[0]["passed"] is True


@pytest.mark.parametrize("polygon",[
    ((20,10),(30,10),(30,24),(20,24)),
    ((10,8),(22,8),(22,26),(10,26)),
])
def test_post_ocr_horizontal_or_height_drift_fails(polygon):
    wrong=candidate("CS",polygon)
    passed,results,_=pipeline._post_validate((wrong,),(_edit_geometry(),),(80,40))
    assert passed is False and results[0]["new_text_matches"]==0


def test_install_secret_is_concurrent_and_persistent(tmp_path):
    assert hasattr(pipeline,"_load_or_create_secret")
    state=tmp_path/"app-state"
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(lambda _: pipeline._load_or_create_secret(state),range(16)))
    assert len(set(values))==1 and len(values[0])==32
    assert pipeline._load_or_create_secret(state)==values[0]
    env={**os.environ,"EDIT_CHART_TEXT_STATE_DIR":str(state),"PYTHONPATH":str(Path(__file__).parents[1]/"src")}
    output=subprocess.check_output([sys.executable,"-c","import edit_chart_text.pipeline as p; print(p._load_or_create_secret(p._state_dir()).hex())"],env=env,text=True)
    assert output.strip()==values[0].hex()


def test_synchronized_report_and_request_tampering_fails_hmac(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((candidate(),candidate(polygon=((40,10),(60,10),(60,24),(40,24))))))
    payload=json.loads(Path(first.report_path).read_text(encoding="utf-8"))
    payload["run_id"]="tampered-run"
    payload["edits"][0]["run_id"]="tampered-run"
    payload["edits"][0]["new_text"]="AB"
    payload["edits"][0]["candidate_number"]=999
    Path(first.report_path).write_text(json.dumps(payload),encoding="utf-8")
    record=payload["edits"][0]
    tampered=EditRequest(source,(Replacement("HZ","AB","one",candidate_number=999,candidate_polygon=tuple(map(tuple,record["polygon"])),candidate_token=record["candidate_token"]),),Path(first.report_path))
    result=run_pipeline(tampered,SequenceOCR((candidate(),),(candidate("AB"),)))
    assert result.status=="needs_confirmation"
    assert "authentication" in " ".join(result.messages).lower()


def test_confirmation_token_rejects_replaced_install_secret(tmp_path):
    source=chart(tmp_path)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((candidate(),candidate(polygon=((40,10),(60,10),(60,24),(40,24))))))
    secret=tmp_path/"app-state"/"install-secret.bin"
    secret.write_bytes(b"x"*32)
    result=run_pipeline(confirmed_request(source,first),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert result.status=="needs_confirmation"
    assert "authentication" in " ".join(result.messages).lower()


def test_formal_paths_do_not_exist_while_transaction_is_running(tmp_path,monkeypatch):
    source=chart(tmp_path); run_id="e"*32
    monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: run_id)
    finals=[tmp_path/f"chart_{run_id}_edited.png",tmp_path/f"chart_{run_id}_edit-report.json",tmp_path/f"chart_{run_id}_candidates.png"]
    class InspectingOCR:
        def __init__(self): self.calls=0
        def detect(self,path):
            self.calls+=1
            if self.calls==1: assert not any(final.exists() for final in finals)
            return (candidate(),) if self.calls==1 else (candidate("CS"),)
    result=run_pipeline(request(source),InspectingOCR())
    assert result.status=="success"


def test_preexisting_final_causes_safe_failure_without_overwrite(tmp_path,monkeypatch):
    source=chart(tmp_path); run_id="f"*32
    monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: run_id)
    existing=tmp_path/f"chart_{run_id}_edited.png"; existing.write_bytes(b"user-output")
    result=run_pipeline(request(source),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert result.status=="failed"
    assert existing.read_bytes()==b"user-output"
    report_path=tmp_path/f"chart_{run_id}_edit-report.json"
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"]=="failed"


def test_preexisting_report_publish_collision_removes_own_output(tmp_path,monkeypatch):
    source=chart(tmp_path); run_id="1"*32
    monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: run_id)
    existing=tmp_path/f"chart_{run_id}_edit-report.json"; existing.write_bytes(b"user-report")
    with pytest.raises(OSError):
        run_pipeline(request(source),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert existing.read_bytes()==b"user-report"
    assert not (tmp_path/f"chart_{run_id}_edited.png").exists()


def test_hidden_marker_collision_retries_new_run_id(tmp_path,monkeypatch):
    source=chart(tmp_path); collided="2"*32; fresh="3"*32
    marker=tmp_path/f".chart_{collided}.edit-chart-text.reserve"; marker.write_bytes(b"existing-marker")
    ids=iter((collided,fresh)); monkeypatch.setattr(pipeline.secrets,"token_hex",lambda _n: next(ids))
    result=run_pipeline(request(source),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert result.status=="success" and result.run_id==fresh
    assert marker.read_bytes()==b"existing-marker"
