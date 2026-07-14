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
    source=chart(tmp_path); original=source.read_bytes(); destinations=[]; real=pipeline.os.link
    def observe(src,dst): destinations.append(Path(dst)); return real(src,dst)
    monkeypatch.setattr(pipeline.os,"link",observe)
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


def test_repeated_substring_reports_each_occurrence_with_full_label_identity(tmp_path):
    source=chart(tmp_path)
    repeated=candidate("HZ-HZ")
    request=EditRequest(
        source,
        (Replacement("HZ","CS","all",match_mode="substring"),),
    )

    report=run_pipeline(request,SequenceOCR((repeated,)))

    assert report.status=="needs_confirmation"
    assert [edit["substring_occurrence"] for edit in report.edits]==[1,2]
    assert [edit["source_label"] for edit in report.edits]==["HZ-HZ","HZ-HZ"]
    assert [edit["target_label"] for edit in report.edits]==["CS-HZ","HZ-CS"]
    assert [edit["match_mode"] for edit in report.edits]==["substring","substring"]
    assert len({edit["candidate_number"] for edit in report.edits})==1
    assert len({tuple(map(tuple,edit["polygon"])) for edit in report.edits})==1
    assert len({edit["candidate_token"] for edit in report.edits})==2


def test_substring_all_renders_and_validates_complete_target_labels(tmp_path, monkeypatch):
    source=tmp_path/"chart.png"
    image=Image.new("RGB",(360,50),"white")
    draw=ImageDraw.Draw(image)
    draw.text((10,10),"HZ25-4DPP",fill="black")
    draw.text((190,10),"HZ25-8DPP",fill="black")
    image.save(source)
    first=TextCandidate("HZ25-4DPP",((10,10),(160,10),(160,30),(10,30)),.99)
    second=TextCandidate("HZ25-8DPP",((190,10),(340,10),(340,30),(190,30)),.99)
    rendered=[]
    real_render=pipeline.render_replacement

    def capture_render(image,item,text,style,allowed):
        rendered.append(text)
        return real_render(image,item,text,style,allowed)

    monkeypatch.setattr(pipeline,"render_replacement",capture_render)
    report=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR(
            (first,second),
            (
                TextCandidate("CS25-4DPP",first.polygon,.99),
                TextCandidate("CS25-8DPP",second.polygon,.99),
            ),
        ),
    )

    assert report.status=="success",report.messages
    assert rendered==["CS25-4DPP","CS25-8DPP"]
    assert [edit["source_label"] for edit in report.edits]==["HZ25-4DPP","HZ25-8DPP"]
    assert [edit["target_label"] for edit in report.edits]==["CS25-4DPP","CS25-8DPP"]
    assert [edit["match_mode"] for edit in report.edits]==["substring","substring"]
    assert [edit["substring_occurrence"] for edit in report.edits]==[1,1]
    assert all(edit["post_ocr_validation"]["passed"] for edit in report.edits)
    assert [edit["post_ocr_validation"]["source_label"] for edit in report.edits]==["HZ25-4DPP","HZ25-8DPP"]
    assert [edit["post_ocr_validation"]["target_label"] for edit in report.edits]==["CS25-4DPP","CS25-8DPP"]

def test_substring_post_ocr_normalizes_expected_label_whitespace_without_changing_audit(tmp_path):
    source=tmp_path/"chart.png"
    image=Image.new("RGB",(200,50),"white")
    ImageDraw.Draw(image).text((10,10),"HZ25-4DPP",fill="black")
    image.save(source)
    original=TextCandidate(" HZ25-4DPP ",((10,10),(190,10),(190,30),(10,30)),.99)

    report=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","one",match_mode="substring"),)),
        SequenceOCR((original,),(TextCandidate("CS25-4DPP",original.polygon,.99),)),
    )

    assert report.status=="success",report.messages
    edit=report.edits[0]
    assert edit["source_label"]==" HZ25-4DPP "
    assert edit["target_label"]==" CS25-4DPP "
    assert edit["post_ocr_validation"]["source_label"]==" HZ25-4DPP "
    assert edit["post_ocr_validation"]["target_label"]==" CS25-4DPP "

def test_substring_confirmation_reconfirms_when_occurrence_disappears(tmp_path):
    source=chart(tmp_path)
    repeated=candidate("HZ-HZ")
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
            substring_occurrence=2,
        ),),
        Path(first.report_path),
    )

    result=run_pipeline(confirmed,SequenceOCR((candidate("HZ"),),(candidate("CS"),)))

    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert "reconfirm" in " ".join(result.messages).lower()


def test_substring_confirmation_reconfirms_when_signed_label_drifts(tmp_path):
    source=chart(tmp_path)
    repeated=candidate("HZ-HZ")
    first=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((repeated,)),
    )
    chosen=first.edits[0]
    confirmed=EditRequest(
        source,
        (Replacement(
            "HZ","CS","one",
            candidate_number=chosen["candidate_number"],
            candidate_polygon=tuple(map(tuple,chosen["polygon"])),
            candidate_token=chosen["candidate_token"],
            match_mode="substring",
            substring_occurrence=1,
        ),),
        Path(first.report_path),
    )

    drifted=candidate("HZ-DRIFT")
    result=run_pipeline(
        confirmed,
        SequenceOCR((drifted,),(candidate("CS-DRIFT"),)),
    )

    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert "reconfirm" in " ".join(result.messages).lower()


def test_substring_scope_all_rejects_partial_edit_when_matching_candidate_is_unsafe(tmp_path):
    source=chart(tmp_path)
    valid=candidate("HZ-A")
    unsafe=TextCandidate("HZ-B",((-1,10),(20,10),(20,24),(-1,24)),.99)

    result=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((valid,unsafe),(candidate("CS-A"),)),
    )

    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert result.edits==[]
    message=" ".join(result.messages).lower()
    assert "unsafe" in message and "matching candidate" in message

def test_substring_scope_all_ignores_unrelated_unsafe_candidate(tmp_path):
    source=chart(tmp_path)
    valid=candidate("HZ-A")
    unrelated=TextCandidate("P10",((-1,10),(20,10),(20,24),(-1,24)),.99)

    result=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","all",match_mode="substring"),)),
        SequenceOCR((valid,unrelated),(candidate("CS-A"),)),
    )

    assert result.status=="success",result.messages

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
    source=chart(tmp_path); other=tmp_path/"chart_other_edited.png"; other.write_bytes(b"keep"); real=pipeline.os.link
    def fail(src,dst):
        if Path(dst).name.endswith("_edit-report.json"): raise OSError("report failed")
        return real(src,dst)
    monkeypatch.setattr(pipeline.os,"link",fail)
    with pytest.raises(OSError,match="report failed"):
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((candidate(),),(candidate("CS"),)))
    assert other.read_bytes()==b"keep"
    assert not [p for p in tmp_path.glob("chart_*_edited.png") if p != other]


def test_report_failure_chains_original_ocr_error(tmp_path,monkeypatch):
    source=chart(tmp_path); real=pipeline.os.link
    class Broken:
        def detect(self,_): raise ValueError("ocr failed")
    def fail(src,dst):
        if Path(dst).name.endswith("_edit-report.json"): raise OSError("report failed")
        return real(src,dst)
    monkeypatch.setattr(pipeline.os,"link",fail)
    with pytest.raises(OSError) as raised:
        run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),Broken())
    assert isinstance(raised.value.__cause__,ValueError)


def test_out_of_bounds_candidate_requires_confirmation_without_edit(tmp_path):
    source=chart(tmp_path)
    bad=TextCandidate("HZ",((-1,10),(20,10),(20,24),(-1,24)),.99)
    report=run_pipeline(EditRequest(source,(Replacement("HZ","CS","one"),)),SequenceOCR((bad,)))
    assert report.status=="needs_confirmation" and report.output_path is None
    assert report.edits == []
