"""Pixel-level safety checks for chart edits."""

from collections.abc import Iterable

import numpy as np
from PIL import Image


def _pixels(image: Image.Image | np.ndarray) -> np.ndarray:
    return np.asarray(image)


def unchanged_outside(
    before: Image.Image | np.ndarray,
    after: Image.Image | np.ndarray,
    boxes: Iterable[tuple[int, int, int, int]],
) -> bool:
    """Return true when every changed pixel is inside at least one box.

    Bounds use Pillow's half-open ``(left, top, right, bottom)`` convention.
    Boxes are clamped to the image so invalid/outside coordinates never grant
    permission to modify unrelated pixels.
    """
    original = _pixels(before)
    edited = _pixels(after)
    if original.shape != edited.shape or original.ndim < 2:
        return False

    height, width = original.shape[:2]
    allowed = np.zeros((height, width), dtype=bool)
    for box in boxes:
        if len(box) != 4:
            continue
        left, top, right, bottom = (int(value) for value in box)
        left, right = max(0, left), min(width, right)
        top, bottom = max(0, top), min(height, bottom)
        if left < right and top < bottom:
            allowed[top:bottom, left:right] = True

    changed = np.any(original != edited, axis=tuple(range(2, original.ndim)))
    return not bool(np.any(changed & ~allowed))
