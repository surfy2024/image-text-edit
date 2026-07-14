import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline
from edit_chart_text.request_io import load_request


def candidate(text="HYSY115FPS0", polygon=None, confidence=.99):
    return TextCandidate(text, polygon or ((10,10),(190,10),(190,30),(10,30)), confidence)


class SequenceOCR:
    def __init__(self, *results): self.results=list(results)
    def detect(self, _path): return self.results.pop(0)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("EDIT_CHART_TEXT_STATE_DIR",str(tmp_path/"app-state"))


def chart(tmp_path):
    path=tmp_path/"chart.png"
    image=Image.new("RGB",(220,50),"white")
    ImageDraw.Draw(image).text((10,10),"HYSY115FPSO",fill="black")
    image.save(path)
    return path


def selection_payload(**updates):
    item={
        "old_text":"HYSY115","new_text":"HYSY","scope":"one",
        "match_mode":"substring","substring_occurrence":1,
        "candidate_number":1,
        "candidate_polygon":[[10,10],[190,10],[190,30],[10,30]],
        "candidate_token":"v1."+("a"*24)+"."+("b"*64),
        "confirmed_source_label":"HYSY115FPSO",
        "confirmed_target_label":"HYSYFPSO",
    }
    item.update(updates)
    return {"image_path":"chart.png","confirmation_report_path":"prior.json","replacements":[item]}


def write_request(tmp_path,payload):
    path=tmp_path/"request.json"
    path.write_text(json.dumps(payload),encoding="utf-8")
    return path


def test_parser_preserves_valid_confirmed_complete_labels(tmp_path):
    replacement=load_request(write_request(tmp_path,selection_payload())).replacements[0]

    assert replacement.confirmed_source_label=="HYSY115FPSO"
    assert replacement.confirmed_target_label=="HYSYFPSO"

def test_replacement_models_confirmed_complete_labels():
    replacement=Replacement(
        "HYSY115","HYSY","one",match_mode="substring",substring_occurrence=1,
        candidate_number=1,candidate_polygon=((10,10),(190,10),(190,30),(10,30)),
        candidate_token="x"*16,confirmed_source_label="HYSY115FPSO",
        confirmed_target_label="HYSYFPSO",
    )
    assert replacement.confirmed_source_label=="HYSY115FPSO"
    assert replacement.confirmed_target_label=="HYSYFPSO"


@pytest.mark.parametrize("updates",[
    {"confirmed_target_label":None},
    {"confirmed_source_label":None},
    {"confirmed_source_label":""},
    {"confirmed_target_label":""},
    {"match_mode":"exact"},
    {"scope":"all"},
    {"candidate_number":None},
    {"substring_occurrence":None},
])
def test_parser_rejects_invalid_confirmed_label_combinations(tmp_path,updates):
    with pytest.raises(ValueError):
        load_request(write_request(tmp_path,selection_payload(**updates)))


def test_parser_rejects_confirmed_labels_on_first_request(tmp_path):
    payload=selection_payload()
    payload.pop("confirmation_report_path")
    item=payload["replacements"][0]
    for key in ("candidate_number","candidate_polygon","candidate_token","substring_occurrence"):
        item.pop(key)
    with pytest.raises(ValueError):
        load_request(write_request(tmp_path,payload))


def test_parser_rejects_confirmed_target_not_strictly_derived(tmp_path):
    with pytest.raises(ValueError,match="confirmed_target_label"):
        load_request(write_request(tmp_path,selection_payload(confirmed_target_label="ARBITRARY")))


def test_parser_rejects_confirmed_source_missing_selected_occurrence(tmp_path):
    with pytest.raises(ValueError,match="confirmed_source_label"):
        load_request(write_request(tmp_path,selection_payload(
            substring_occurrence=2,confirmed_source_label="HYSY115FPSO",
            confirmed_target_label="HYSYFPSO",
        )))


@pytest.mark.parametrize("ocr_label",["HYSY115FPS0","HYSY115FPSO"])
def test_unique_ambiguous_fps0_substring_requires_visual_confirmation(tmp_path,ocr_label):
    source=chart(tmp_path)
    result=run_pipeline(
        EditRequest(source,(Replacement("HYSY115","HYSY","one",match_mode="substring"),)),
        SequenceOCR((candidate(ocr_label),)),
    )
    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert result.edits[0]["source_label"]==ocr_label
    assert "visually" in " ".join(result.messages).lower()


def test_confirmed_visual_label_override_renders_and_audits_complete_target(tmp_path,monkeypatch):
    source=chart(tmp_path)
    first=run_pipeline(
        EditRequest(source,(Replacement("HYSY115","HYSY","one",match_mode="substring"),)),
        SequenceOCR((candidate("HYSY115FPS0"),)),
    )
    record=first.edits[0]
    confirmed=EditRequest(source,(Replacement(
        "HYSY115","HYSY","one",match_mode="substring",substring_occurrence=1,
        candidate_number=record["candidate_number"],
        candidate_polygon=tuple(map(tuple,record["polygon"])),
        candidate_token=record["candidate_token"],
        confirmed_source_label="HYSY115FPSO",confirmed_target_label="HYSYFPSO",
    ),),Path(first.report_path))
    rendered=[]
    real=pipeline.render_replacement
    def capture(image,item,text,style,allowed):
        rendered.append(text)
        return real(image,item,text,style,allowed)
    monkeypatch.setattr(pipeline,"render_replacement",capture)

    result=run_pipeline(
        confirmed,
        SequenceOCR((candidate("HYSY115FPS0"),),(candidate("HYSYFPSO"),)),
    )

    assert result.status=="success",result.messages
    assert rendered==["HYSYFPSO"]
    edit=result.edits[0]
    assert edit["source_label"]=="HYSY115FPSO"
    assert edit["target_label"]=="HYSYFPSO"
    assert edit["ocr_source_label"]=="HYSY115FPS0"
    assert edit["ocr_target_label"]=="HYSYFPS0"
    assert edit["label_override"] is True


def override_edit():
    return {
        "match_mode":"substring","source_label":"HYSY115FPSO","target_label":"HYSYFPSO",
        "ocr_source_label":"HYSY115FPS0","ocr_target_label":"HYSYFPS0","label_override":True,
        "polygon":[[10,10],[190,10],[190,30],[10,30]],"allowed_box":[8,8,192,32],
    }


def test_post_ocr_fps0_target_does_not_satisfy_confirmed_fpso():
    passed,_,messages=pipeline._post_validate((candidate("HYSYFPS0"),),(override_edit(),),(220,50))
    assert passed is False
    assert any("new_text" in message for message in messages)


def test_post_ocr_rejects_remaining_ocr_source_variant():
    passed,_,messages=pipeline._post_validate(
        (candidate("HYSYFPSO"),candidate("HYSY115FPS0")),(override_edit(),),(220,50)
    )
    assert passed is False
    assert any("old_text" in message for message in messages)


@pytest.mark.parametrize("ocr_label",["HZA0","0AHZ"])
def test_single_letter_next_to_o_or_zero_requires_visual_confirmation(tmp_path,ocr_label):
    source=chart(tmp_path)

    result=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","one",match_mode="substring"),)),
        SequenceOCR((candidate(ocr_label),)),
    )

    assert result.status=="needs_confirmation"
    assert result.output_path is None
    assert "visually" in " ".join(result.messages).lower()

def test_plain_hz_substring_still_auto_ready(tmp_path):
    source=chart(tmp_path)
    hz=candidate("HZ26-6DPP")
    result=run_pipeline(
        EditRequest(source,(Replacement("HZ","CS","one",match_mode="substring"),)),
        SequenceOCR((hz,),(candidate("CS26-6DPP"),)),
    )
    assert result.status=="success",result.messages


def test_exact_mode_is_not_forced_to_confirm_by_ambiguous_suffix(tmp_path):
    source=chart(tmp_path)
    result=run_pipeline(
        EditRequest(source,(Replacement("HYSY115FPS0","HYSYFPS0","one"),)),
        SequenceOCR((candidate("HYSY115FPS0"),),(candidate("HYSYFPS0"),)),
    )
    assert result.status=="success",result.messages
