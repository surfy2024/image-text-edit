"""OCR backends and PaddleOCR result normalization."""

from collections.abc import Callable, Iterable
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from PIL import Image

from .models import TextCandidate


class OCRBackendError(RuntimeError):
    """Raised when the third-party OCR engine cannot be initialized."""


class OCRBackend(Protocol):
    def detect(self, image_path: Path) -> tuple[TextCandidate, ...]: ...


def _candidate(text: Any, score: Any, polygon: Any) -> TextCandidate | None:
    try:
        numeric_score = float(score)
        numeric_points = tuple((float(point[0]), float(point[1])) for point in polygon)
        if (
            not isinstance(text, str)
            or len(numeric_points) < 3
            or not math.isfinite(numeric_score)
            or not 0.0 <= numeric_score <= 1.0
            or not all(math.isfinite(coordinate) for point in numeric_points for coordinate in point)
        ):
            return None
        points = tuple((int(round(x)), int(round(y))) for x, y in numeric_points)
        return TextCandidate(text, points, numeric_score)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_paddle_result(result: Any) -> tuple[TextCandidate, ...]:
    """Parse common PaddleOCR 2.x/3.x structures, ignoring malformed records."""
    parsed: list[TextCandidate] = []

    def visit(value: Any) -> None:
        if hasattr(value, "json"):
            value = value.json
        if isinstance(value, dict):
            texts = value.get("rec_texts")
            scores = value.get("rec_scores")
            polygons = value.get("dt_polys")
            if polygons is None:
                polygons = value.get("rec_polys")
            if texts is not None and scores is not None and polygons is not None:
                for text, score, polygon in zip(texts, scores, polygons):
                    item = _candidate(text, score, polygon)
                    if item is not None:
                        parsed.append(item)
                return
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            if len(value) == 2 and isinstance(value[1], (list, tuple)) and len(value[1]) >= 2:
                item = _candidate(value[1][0], value[1][1], value[0])
                if item is not None:
                    parsed.append(item)
                    return
            for nested in value:
                visit(nested)

    visit(result)
    return tuple(parsed)


def _bounds(candidate: TextCandidate) -> tuple[int, int, int, int]:
    xs = [point[0] for point in candidate.polygon]
    ys = [point[1] for point in candidate.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _iou(left: TextCandidate, right: TextCandidate) -> float:
    lx1, ly1, lx2, ly2 = _bounds(left)
    rx1, ry1, rx2, ry2 = _bounds(right)
    intersection = max(0, min(lx2, rx2) - max(lx1, rx1)) * max(0, min(ly2, ry2) - max(ly1, ry1))
    left_area = max(0, lx2 - lx1) * max(0, ly2 - ly1)
    right_area = max(0, rx2 - rx1) * max(0, ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


class PaddleOCRBackend:
    def __init__(
        self,
        predictor: Callable[[Path], Any] | None = None,
        scales: Iterable[float] | None = None,
    ) -> None:
        self._predictor = predictor
        self._scales = tuple(scales) if scales is not None else None

    def _default_predictor(self) -> Callable[[Path], Any]:
        from paddleocr import PaddleOCR  # lazy: optional models are expensive

        try:
            engine = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )
        except Exception as error:
            raise OCRBackendError(
                "OCR initialization or model acquisition failed; "
                "check local models/cache or network access."
            ) from error
        return lambda path: engine.predict(str(path))

    def detect(self, image_path: Path) -> tuple[TextCandidate, ...]:
        predictor = self._predictor or self._default_predictor()
        with Image.open(image_path) as source:
            source.load()
            width, height = source.size
            scales = self._scales or ((1.0, 2.0) if max(width, height) <= 1600 else (1.0, 1.5))
            found: list[tuple[TextCandidate, set[int]]] = []
            with TemporaryDirectory(prefix="edit-chart-ocr-") as directory:
                temp_dir = Path(directory)
                for index, scale in enumerate(scales):
                    working = source if scale == 1.0 else source.resize(
                        (max(1, round(width * scale)), max(1, round(height * scale)))
                    )
                    temporary = temp_dir / f"scale-{index}.png"
                    working.save(temporary)
                    for item in parse_paddle_result(predictor(temporary)):
                        polygon = tuple(
                            (int(round(x / scale)), int(round(y / scale))) for x, y in item.polygon
                        )
                        normalized = TextCandidate(item.text, polygon, item.confidence)
                        overlaps = (
                            (_iou(existing, normalized), i)
                            for i, (existing, source_indices) in enumerate(found)
                            if index not in source_indices and existing.text == normalized.text
                        )
                        best_iou, duplicate_index = max(
                            overlaps, key=lambda match: match[0], default=(0.0, None)
                        )
                        if duplicate_index is None or best_iou < 0.50:
                            found.append((normalized, {index}))
                        else:
                            existing, source_indices = found[duplicate_index]
                            source_indices.add(index)
                            if normalized.confidence > existing.confidence:
                                found[duplicate_index] = (normalized, source_indices)
            return tuple(candidate for candidate, _ in found)
