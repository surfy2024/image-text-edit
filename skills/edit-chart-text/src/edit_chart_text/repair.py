"""Remove glyph pixels while retaining the local chart background."""

import cv2
import numpy as np
from PIL import Image

from .models import TextCandidate
from .style import candidate_bounds


def _allowed(box, size, padding):
    l, t, r, b = box; w, h = size
    return max(0, l-padding), max(0, t-padding), min(w, r+padding), min(h, b+padding)


def _background_surface(patch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit a robust planar RGB background from permitted-patch border pixels."""
    height, width = patch.shape[:2]
    coordinates = np.concatenate((
        np.column_stack((np.arange(width), np.zeros(width))),
        np.column_stack((np.arange(width), np.full(width, height - 1))),
        np.column_stack((np.zeros(height), np.arange(height))),
        np.column_stack((np.full(height, width - 1), np.arange(height))),
    )).astype(float)
    samples = np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1])).astype(float)
    design = np.column_stack((coordinates, np.ones(len(coordinates))))
    keep = np.ones(len(samples), dtype=bool)
    for _ in range(3):
        coefficients = np.linalg.lstsq(design[keep], samples[keep], rcond=None)[0]
        residual = np.linalg.norm(samples - design @ coefficients, axis=1)
        keep = residual <= np.percentile(residual, 80)
    yy, xx = np.mgrid[:height, :width]
    full_design = np.column_stack((xx.ravel(), yy.ravel(), np.ones(height * width)))
    surface = (full_design @ coefficients).reshape(height, width, 3)
    return surface, residual



def repair_text_region(image: Image.Image, candidate: TextCandidate, padding: int = 2):
    box = candidate_bounds(candidate, image.size)
    l, t, r, b = box
    if r <= l or b <= t:
        raise ValueError("candidate is empty or outside image")
    allowed = _allowed(box, image.size, max(0, int(padding)))
    al, at, ar, ab = allowed
    original = np.asarray(image.convert("RGB")); patch = original[at:ab, al:ar].copy()
    # Estimate background from the permitted patch border, then isolate glyph-like pixels.
    border = np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))
    bg = np.median(border, axis=0)
    surface, border_residual = _background_surface(patch)
    dist = np.linalg.norm(patch.astype(float) - surface, axis=2)
    threshold = min(45.0, max(10.0, float(np.percentile(border_residual, 90)) * 3.0 + 4.0))
    mask = (dist >= threshold).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)

    border_std = float(np.std(cv2.cvtColor(border.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY)))
    side_jump = lambda side: float(np.max(np.abs(np.diff(side.astype(float), axis=0)))) if len(side) > 1 else 0.0
    has_chart_line = ((side_jump(patch[:, 0]) > 60 and side_jump(patch[:, -1]) > 60) or
                      (side_jump(patch[0]) > 60 and side_jump(patch[-1]) > 60))
    if border_std < 5:
        method = "uniform"
        fill = np.empty_like(patch); fill[:] = np.median(border, axis=0).astype(np.uint8)
    elif not has_chart_line and float(np.percentile(border_residual, 75)) < 4.0:
        method = "gradient"
        # Bilinear surface from robust edge colors, preserving either gradient direction.
        left, right = np.median(patch[:, 0], axis=0), np.median(patch[:, -1], axis=0)
        top, bottom = np.median(patch[0], axis=0), np.median(patch[-1], axis=0)
        xx = np.linspace(0, 1, patch.shape[1])[None, :, None]
        yy = np.linspace(0, 1, patch.shape[0])[:, None, None]
        horiz = left[None, None, :] * (1-xx) + right[None, None, :] * xx
        vert = top[None, None, :] * (1-yy) + bottom[None, None, :] * yy
        center = np.median(border, axis=0)[None, None, :]
        fill = np.clip(horiz + vert - center, 0, 255).astype(np.uint8)
    else:
        method = "inpaint"
        fill = cv2.inpaint(patch, mask, 3, cv2.INPAINT_TELEA)
    result_patch = patch.copy()
    result_patch[mask > 0] = fill[mask > 0]
    if method == "inpaint" and has_chart_line:
        cross_rows = np.where((np.linalg.norm(patch[:, 0].astype(float) - bg, axis=1) > 30) &
                              (np.linalg.norm(patch[:, -1].astype(float) - bg, axis=1) > 30))[0]
        cross_cols = np.where((np.linalg.norm(patch[0].astype(float) - bg, axis=1) > 30) &
                              (np.linalg.norm(patch[-1].astype(float) - bg, axis=1) > 30))[0]
        for row in cross_rows:
            x = np.linspace(0, 1, patch.shape[1])[:, None]
            result_patch[row] = patch[row, 0] * (1 - x) + patch[row, -1] * x
        for column in cross_cols:
            y = np.linspace(0, 1, patch.shape[0])[:, None]
            result_patch[:, column] = patch[0, column] * (1 - y) + patch[-1, column] * y
    result = original.copy(); result[at:ab, al:ar] = result_patch
    return Image.fromarray(result), allowed, method

def repair_region(image: Image.Image, candidate: TextCandidate, padding: int = 2):
    """Repair a candidate through the stable pipeline-facing API."""
    return repair_text_region(image, candidate, padding)
