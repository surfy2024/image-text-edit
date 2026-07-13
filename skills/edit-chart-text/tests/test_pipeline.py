import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


def candidate(text="HZ", x=10):
    return TextCandidate(text, ((x,10),(x+20,10),(x+20,24),(x,24)), .99)


class SequenceOCR:
    def __init__(self, *results): self.results=list(results)
    def detect(self, _path): return self.results.pop(0)


def chart(tmp_path, second=False):
    path=tmp_path/"chart.png"; image=Image.new("RGB",(80,40),"white"); draw=ImageDraw.Draw(image)
    draw.text((10,10),"HZ",fill="black")
    if second: draw.text((40,10),"HZ",fill="black")
    image.save(path); return path


def confirm(source, first, index, old="HZ", new="CS"):
    record=first.edits[index]
    return EditRequest(source,(Replacement(old,new,"one",candidate_number=record["candidate_number"],
        candidate_polygon=tuple(map(tuple,record["polygon"])),candidate_token=record["candidate_token"]),),Path(first.report_path))


def test_success_preserves_source_and_publishes_unique_report_last(tmp_path, monkeypatch):
    source=chart(tmp_path); original=source.read_bytes(); destinations=[]; real=pipeline.os.replace
    def observe(src,dst): destinations.append(Path(dst)); return real(src,dst)
    monkeypatch.setattr(pipeline.os,"replace",observe)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert report.status=="success"
    assert source.read_bytes()==original
    assert Path(report.output_path).exists() and Path(report.report_path).exists()
    assert destinations[-1]==Path(report.report_path)
    assert json.loads(Path(report.report_path).read_text(encoding="utf-8"))["run_id"]==report.run_id


def test_ambiguous_ask_publishes_only_unique_preview_and_report(tmp_path):
    source=chart(tmp_path,second=True)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((candidate(x=10),candidate(x=40))))
    assert report.status=="needs_confirmation"
    assert report.output_path is None
    assert Path(report.preview_path).exists() and Path(report.report_path).exists()
    assert [e["candidate_number"] for e in report.edits]==[1,2]
    assert all(e["candidate_token"] for e in report.edits)


def test_fuzzy_match_never_auto_edits(tmp_path):
    source=chart(tmp_path)
    fuzzy=TextCandidate("HZZ",candidate().polygon,.99)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((fuzzy,)))
    assert report.status=="needs_confirmation" and report.output_path is None


def test_confirmed_token_survives_reversed_ocr_order_and_small_drift(tmp_path):
    source=chart(tmp_path,second=True)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((candidate(x=10),candidate(x=40))))
    request=confirm(source,first,1)
    second=run_pipeline(request,SequenceOCR((candidate(x=41),candidate(x=10)),(candidate("CS",x=41),)))
    assert second.status=="success",second.messages
    assert second.edits[0]["polygon"]==[list(p) for p in candidate(x=41).polygon]


def test_large_drift_or_two_close_matches_requires_reconfirmation(tmp_path):
    source=chart(tmp_path,second=True)
    first=run_pipeline(EditRequest(source,(Replacement("HZ","CS","ask"),)),SequenceOCR((candidate(x=10),candidate(x=40))))
    for detected in ((candidate(x=10),candidate(x=58)),(candidate(x=39),candidate(x=41))):
        result=run_pipeline(confirm(source,first,1),SequenceOCR(detected))
        assert result.status=="needs_confirmation" and result.output_path is None


def test_overlapping_ready_candidates_require_confirmation_before_edit(tmp_path):
    source=chart(tmp_path)
    p10=TextCandidate("P10",((25,12),(45,12),(45,26),(25,26)),.99)
    request=EditRequest(source,(Replacement("HZ","CS","one"),Replacement("P10","P20","one")))
    result=run_pipeline(request,SequenceOCR((candidate(),p10)))
    assert result.status=="needs_confirmation" and Path(result.preview_path).exists()


def test_report_failure_cleans_only_this_runs_output_and_chains_processing_error(tmp_path,monkeypatch):
    source=chart(tmp_path); other=tmp_path/"chart_other_edited.png"; other.write_bytes(b"keep"); real=pipeline.os.replace
    def fail(src,dst):
        if Path(dst).name.endswith("_edit-report.json"): raise OSError("report failed")
        return real(src,dst)
    monkeypatch.setattr(pipeline.os,"replace",fail)
    with pytest.raises(OSError,match="report failed"):
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert other.read_bytes()==b"keep"
    assert not [p for p in tmp_path.glob("chart_*_edited.png") if p != other]


def test_report_failure_chains_original_ocr_error(tmp_path,monkeypatch):
    source=chart(tmp_path); real=pipeline.os.replace
    class Broken:
        def detect(self,_): raise ValueError("ocr failed")
    def fail(src,dst):
        if Path(dst).name.endswith("_edit-report.json"): raise OSError("report failed")
        return real(src,dst)
    monkeypatch.setattr(pipeline.os,"replace",fail)
    with pytest.raises(OSError) as raised:
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),Broken())
    assert isinstance(raised.value.__cause__,ValueError)


def test_out_of_bounds_candidate_fails_without_edit(tmp_path):
    source=chart(tmp_path)
    bad=TextCandidate("HZ",((-1,10),(20,10),(20,24),(-1,24)),.99)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((bad,)))
    assert report.status=="failed" and report.output_path is None
