from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

cv2 = pytest.importorskip("cv2", reason="opencv-python-headless is a declared dependency")

from edit_chart_text.models import TextCandidate, TextStyle
from edit_chart_text.repair import repair_region, repair_text_region
from edit_chart_text.render import render_replacement, render_text
from edit_chart_text.style import candidate_bounds, estimate_style, estimate_text_style


def candidate(box=(10, 8, 30, 22)):
    l, t, r, b = box
    return TextCandidate("HZ", ((l, t), (r, t), (r, b), (l, b)), 0.99)


def text_image(background=(245, 245, 245), color=(20, 40, 80)):
    im = Image.new("RGB", (50, 32), background)
    ImageDraw.Draw(im).text((12, 9), "HZ", fill=color)
    return im


@pytest.mark.parametrize("box", [(-1, 3, 20, 15), (3, -1, 20, 15), (3, 3, 50, 15), (3, 3, 20, 32), (60, 40, 70, 50)])
def test_candidate_bounds_reject_any_out_of_image_vertex(box):
    with pytest.raises(ValueError, match="inside image"):
        candidate_bounds(candidate(box), (50, 32))


def test_candidate_bounds_accept_valid_edge_touching_polygon():
    assert candidate_bounds(candidate((0, 0, 49, 31)), (50, 32)) == (0, 0, 49, 31)


def test_estimates_glyph_color_and_integer_font_size():
    style = estimate_text_style(text_image(), candidate())
    assert max(abs(a - b) for a, b in zip(style.color_rgb, (20, 40, 80))) < 25
    assert isinstance(style.font_size, int) and style.font_size >= 6


@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_gradient_repair_is_not_a_rectangular_patch(axis):
    a = np.zeros((32, 50, 3), dtype=np.uint8)
    ramp = np.linspace(80, 220, 50 if axis == "horizontal" else 32).astype(np.uint8)
    if axis == "horizontal":
        a[:] = ramp[None, :, None]
    else:
        a[:] = ramp[:, None, None]
    im = Image.fromarray(a)
    ImageDraw.Draw(im).text((12, 9), "HZ", fill=(5, 5, 5))
    repaired, allowed, method = repair_text_region(im, candidate(), padding=2)
    assert method == "gradient"
    out = np.asarray(repaired)
    assert np.unique(out[allowed[1]:allowed[3], allowed[0]:allowed[2], 0]).size > 4
    assert np.array_equal(out[:allowed[1]], a[:allowed[1]])


def test_uniform_repair_removes_text_and_confines_changes():
    im = text_image()
    before = np.asarray(im).copy()
    repaired, allowed, method = repair_text_region(im, candidate(), padding=2)
    out = np.asarray(repaired)
    assert method == "uniform"
    assert np.mean(np.abs(out[9:22].astype(int) - 245)) < 3
    mask = np.ones(before.shape[:2], bool)
    l, t, r, b = allowed
    mask[t:b, l:r] = False
    assert np.array_equal(out[mask], before[mask])


def test_line_continuity_is_approximately_preserved():
    im = Image.new("RGB", (50, 32), "white")
    d = ImageDraw.Draw(im)
    d.line((0, 16, 49, 16), fill=(30, 30, 30), width=2)
    d.text((12, 9), "HZ", fill=(0, 0, 0))
    repaired, _, method = repair_text_region(im, candidate(), padding=2)
    row = np.asarray(repaired)[16, :, 0]
    assert method == "inpaint"
    assert (row < 100).sum() >= 35


@pytest.mark.parametrize("replacement", ["C", "CS", "CSS"])
def test_render_short_same_and_slightly_longer_text(replacement):
    im = Image.new("RGB", (60, 32), "white")
    out = render_text(im, replacement, TextStyle((10, 20, 30), 14), (10, 8, 30, 24), (8, 6, 45, 26))
    a, b = np.asarray(im), np.asarray(out)
    assert np.any(a != b)
    outside = np.ones(a.shape[:2], bool); outside[6:26, 8:45] = False
    assert np.array_equal(a[outside], b[outside])


def test_impossible_text_and_bad_candidates_raise():
    im = Image.new("RGB", (30, 20), "white")
    with pytest.raises(ValueError, match="does not fit safely"):
        render_text(im, "THIS CANNOT FIT", TextStyle((0, 0, 0), 14), (5, 5, 10, 10), (5, 5, 10, 10))
    for c in (candidate((4, 4, 4, 8)), candidate((40, 40, 50, 50))):
        with pytest.raises(ValueError):
            repair_text_region(im, c)

def test_render_rejects_target_outside_image_or_allowed_box():
    im = Image.new("RGB", (30, 20), "white")
    style = TextStyle((0, 0, 0), 12)
    with pytest.raises(ValueError, match="invalid target box"):
        render_text(im, "CS", style, (100, 100, 110, 110), (2, 2, 20, 18))
    with pytest.raises(ValueError, match="invalid target box"):
        render_text(im, "CS", style, (1, 3, 12, 14), (2, 2, 20, 18))

def test_public_pipeline_contract():
    im = text_image()
    item = candidate()
    style = estimate_style(im, item)
    repaired, allowed, method = repair_region(im, item)
    output = render_replacement(repaired, item, "CS", style, allowed)
    assert method in {"uniform", "gradient", "inpaint"}
    assert output.size == im.size


def test_dense_bold_foreground_is_removed():
    im = Image.new("RGB", (40, 28), (230, 230, 230))
    ImageDraw.Draw(im).rectangle((10, 7, 29, 20), fill=(15, 15, 15))
    item = candidate((9, 6, 31, 22))
    repaired, _, method = repair_region(im, item, padding=2)
    center = np.asarray(repaired)[9:19, 12:28]
    assert method == "uniform"
    assert center.mean() > 220


def test_long_glyph_stroke_removed_without_crossing_border_evidence():
    im = Image.new("RGB", (50, 32), "white")
    ImageDraw.Draw(im).line((11, 15, 29, 15), fill=(0, 0, 0), width=3)
    repaired, _, method = repair_region(im, candidate(), padding=2)
    assert method == "uniform"
    assert np.asarray(repaired)[15, 12:29, 0].mean() > 240


def test_real_long_label_repair_removes_antialiased_glyph_ghosts():
    source = Path(__file__).parent / "fixtures" / "chart_sample.png"
    image = Image.open(source).convert("RGB")
    item = TextCandidate(
        "HYSY FPSO",
        ((210, 246), (296, 246), (296, 266), (210, 266)),
        0.997,
    )

    repaired, _, method = repair_region(image, item, padding=2)

    assert method == "inpaint"
    core = np.asarray(repaired)[246:266, 210:296]
    gray = cv2.cvtColor(core, cv2.COLOR_RGB2GRAY).astype(float)
    high_frequency = np.abs(gray - cv2.GaussianBlur(gray, (0, 0), 2))
    assert np.percentile(high_frequency, 99) < 5.0

    original_patch = np.asarray(image)[244:268, 208:298]
    repaired_patch = np.asarray(repaired)[244:268, 208:298]
    vertical_weight = np.linspace(0, 1, original_patch.shape[0])[:, None, None]
    border_interpolation = (
        original_patch[0][None, :, :] * (1 - vertical_weight)
        + original_patch[-1][None, :, :] * vertical_weight
    )
    block_deviation = np.linalg.norm(
        repaired_patch.astype(float) - border_interpolation, axis=2
    )[:, 2:88]
    assert np.percentile(block_deviation, 90) < 7.0


def test_rgba_repair_and_render_preserve_alpha_everywhere():
    rgba = text_image().convert("RGBA")
    alpha = np.arange(rgba.width * rgba.height, dtype=np.uint16).reshape(rgba.height, rgba.width) % 256
    rgba.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    item = candidate()
    repaired, allowed, _ = repair_region(rgba, item)
    output = render_replacement(repaired, item, "CS", estimate_style(rgba, item), allowed)
    assert output.mode == "RGBA"
    assert np.array_equal(np.asarray(output)[:, :, 3], alpha.astype(np.uint8))
