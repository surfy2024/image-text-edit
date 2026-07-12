from pathlib import Path
import tempfile

from PIL import Image
import pytest

from edit_chart_text.ocr import PaddleOCRBackend, parse_paddle_result


def payload(text: str, score: float, box: list[list[float]]) -> dict:
    return {"rec_texts": [text], "rec_scores": [score], "dt_polys": [box]}


def test_parser_handles_paddle3_mapping() -> None:
    result = parse_paddle_result([payload("A", .9, [[1, 2], [3, 2], [3, 4], [1, 4]])])
    assert result[0].text == "A"
    assert result[0].polygon == ((1, 2), (3, 2), (3, 4), (1, 4))




def test_parser_skips_non_finite_and_invalid_scores_but_keeps_valid_siblings() -> None:
    box = [[1, 1], [5, 1], [5, 5], [1, 5]]
    result = parse_paddle_result([{
        "rec_texts": ["coord-inf", "coord-nan", "score-inf", "score-nan", "too-high", "valid"],
        "rec_scores": [.8, .8, float("inf"), float("nan"), 1.2, .75],
        "dt_polys": [
            [[float("inf"), 1], [5, 1], [5, 5], [1, 5]],
            [[float("nan"), 1], [5, 1], [5, 5], [1, 5]],
            box, box, box, box,
        ],
    }])
    assert [item.text for item in result] == ["valid"]


def test_scale_normalization_dedup_and_highest_confidence(tmp_path: Path) -> None:
    source = tmp_path / "chart.png"
    Image.new("RGB", (100, 80), "white").save(source)
    original = source.read_bytes()
    calls = 0

    def predictor(path: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [payload("A", .82, [[10, 10], [30, 10], [30, 30], [10, 30]])]
        return [payload("A", .95, [[22, 20], [62, 20], [62, 60], [22, 60]])]

    result = PaddleOCRBackend(predictor=predictor, scales=(1.0, 2.0)).detect(source)
    assert len(result) == 1
    assert result[0].confidence == .95
    assert result[0].polygon == ((11, 10), (31, 10), (31, 30), (11, 30))
    assert source.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chart.png"]


def test_distinct_same_text_boxes_remain_stable(tmp_path: Path) -> None:
    source = tmp_path / "chart.png"
    Image.new("RGB", (100, 80), "white").save(source)
    result = PaddleOCRBackend(
        predictor=lambda _: [payload("A", .9, [[1, 1], [11, 1], [11, 11], [1, 11]]),
                             payload("A", .8, [[50, 1], [60, 1], [60, 11], [50, 11]])],
        scales=(1.0,),
    ).detect(source)
    assert [item.polygon[0][0] for item in result] == [1, 50]


def test_temporary_files_are_cleaned_on_predictor_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "chart.png"
    temp_root = tmp_path / "temporary-root"
    temp_root.mkdir()
    Image.new("RGB", (20, 20), "white").save(source)
    original = source.read_bytes()

    monkeypatch.setattr(
        "edit_chart_text.ocr.TemporaryDirectory",
        lambda prefix: tempfile.TemporaryDirectory(prefix=prefix, dir=temp_root),
    )

    def fail(_: Path):
        raise RuntimeError("ocr failed")

    with pytest.raises(RuntimeError, match="ocr failed"):
        PaddleOCRBackend(predictor=fail, scales=(2.0,)).detect(source)
    assert source.read_bytes() == original
    assert list(temp_root.iterdir()) == []

def test_cross_scale_dedup_preserves_two_same_scale_labels(tmp_path: Path) -> None:
    source = tmp_path / "chart.png"
    Image.new("RGB", (120, 80), "white").save(source)
    calls = 0

    def predictor(_: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                payload("A", .80, [[10, 10], [30, 10], [30, 30], [10, 30]]),
                payload("A", .85, [[40, 10], [60, 10], [60, 30], [40, 30]]),
            ]
        return [
            payload("A", .95, [[22, 20], [62, 20], [62, 60], [22, 60]]),
            payload("A", .90, [[82, 20], [122, 20], [122, 60], [82, 60]]),
        ]

    result = PaddleOCRBackend(predictor=predictor, scales=(1.0, 2.0)).detect(source)
    assert len(result) == 2
    assert [item.confidence for item in result] == [.95, .90]
    assert [item.polygon[0] for item in result] == [(11, 10), (41, 10)]
