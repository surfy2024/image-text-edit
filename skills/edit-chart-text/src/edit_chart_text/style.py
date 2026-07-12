"""Deterministic extraction of text geometry and appearance."""

import numpy as np
from PIL import Image

from .models import TextCandidate, TextStyle


def candidate_bounds(candidate: TextCandidate, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    if not candidate.polygon:
        raise ValueError("candidate has no polygon")
    width, height = image_size
    xs, ys = zip(*candidate.polygon)
    return (min(width, max(0, min(xs))), min(height, max(0, min(ys))),
            min(width, max(0, max(xs))), min(height, max(0, max(ys))))


def estimate_text_style(image: Image.Image, candidate: TextCandidate) -> TextStyle:
    box = candidate_bounds(candidate, image.size)
    l, t, r, b = box
    if r <= l or b <= t:
        raise ValueError("candidate is empty or outside image")
    crop = np.asarray(image.convert("RGB"))[t:b, l:r].astype(np.float32)
    if crop.size == 0:
        raise ValueError("candidate crop is empty")
    border = np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1]), axis=0)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(crop - background, axis=2)
    threshold = max(18.0, float(np.percentile(distance, 90)))
    glyph = crop[distance >= threshold]
    color = np.median(glyph, axis=0) if glyph.size else np.median(crop.reshape(-1, 3), axis=0)
    # OCR boxes include modest leading; height is a stable MVP proxy.
    font_size = max(6, int(round((b - t) * 0.8)))
    return TextStyle(tuple(int(np.clip(round(v), 0, 255)) for v in color), font_size, 0.0)


def estimate_style(image: Image.Image, candidate: TextCandidate) -> TextStyle:
    """Public style-estimation API used by the editing pipeline."""
    return estimate_text_style(image, candidate)
