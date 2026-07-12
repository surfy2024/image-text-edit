# Edit Chart Text Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Codex Skill that turns a user-specified old-text/new-text replacement into a controlled, validated edit of scientific chart images without overwriting the source.

**Architecture:** Keep natural-language interpretation in `SKILL.md`, normalize it into a JSON request, and pass that request to a deterministic Python pipeline. The pipeline isolates OCR, candidate selection, style estimation, background repair, rendering, validation, and reporting behind small interfaces so each stage can be tested with synthetic images and fake OCR results.

**Tech Stack:** Python 3.11+, PaddleOCR 3.x, OpenCV, Pillow, NumPy, pytest, Codex Skill metadata.

---

## File map

- `skills/edit-chart-text/SKILL.md`: trigger description, user interaction, ambiguity rules, and script invocation.
- `skills/edit-chart-text/agents/openai.yaml`: Codex UI metadata.
- `skills/edit-chart-text/pyproject.toml`: runtime and test dependencies plus CLI entry point.
- `skills/edit-chart-text/src/edit_chart_text/models.py`: request, candidate, style, edit, and report data structures.
- `skills/edit-chart-text/src/edit_chart_text/request_io.py`: parse and validate JSON requests.
- `skills/edit-chart-text/src/edit_chart_text/ocr.py`: PaddleOCR adapter and multi-scale coordinate normalization.
- `skills/edit-chart-text/src/edit_chart_text/matching.py`: exact and candidate-only fuzzy matching.
- `skills/edit-chart-text/src/edit_chart_text/style.py`: local text style and background estimation.
- `skills/edit-chart-text/src/edit_chart_text/repair.py`: glyph mask creation and background repair.
- `skills/edit-chart-text/src/edit_chart_text/render.py`: font selection, text fitting, and drawing.
- `skills/edit-chart-text/src/edit_chart_text/validate.py`: OCR and pixel-boundary quality checks.
- `skills/edit-chart-text/src/edit_chart_text/pipeline.py`: orchestration and safe output behavior.
- `skills/edit-chart-text/src/edit_chart_text/cli.py`: command-line interface and exit codes.
- `skills/edit-chart-text/references/troubleshooting.md`: actionable failure guidance loaded only when needed.
- `skills/edit-chart-text/tests/`: focused unit and integration tests.
- `skills/edit-chart-text/tests/fixtures/chart_sample.png`: copied test fixture derived from the user-provided example when execution begins.

### Task 1: Scaffold and validate the Skill package

**Files:**
- Create: `skills/edit-chart-text/SKILL.md`
- Create: `skills/edit-chart-text/agents/openai.yaml`
- Create: `skills/edit-chart-text/pyproject.toml`
- Create: `skills/edit-chart-text/src/edit_chart_text/__init__.py`
- Create: `skills/edit-chart-text/tests/test_package.py`

- [ ] **Step 1: Initialize the Skill with the official scaffold**

Run:

```powershell
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\init_skill.py edit-chart-text --path skills --resources scripts,references,assets --interface "display_name=Edit Chart Text" --interface "short_description=Replace text in scientific chart images without altering surrounding graphics" --interface "default_prompt=Use $edit-chart-text to replace specified text in this chart image while preserving the original image and surrounding pixels."
```

Expected: `skills/edit-chart-text/` is created with `SKILL.md` and `agents/openai.yaml`.

- [ ] **Step 2: Write the failing package test**

Create `skills/edit-chart-text/tests/test_package.py`:

```python
from edit_chart_text import __version__


def test_package_version():
    assert __version__ == "0.1.0"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest skills/edit-chart-text/tests/test_package.py -v`

Expected: FAIL because `edit_chart_text` is not installed or `__version__` is absent.

- [ ] **Step 4: Add packaging and the minimal module**

Replace `skills/edit-chart-text/pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "edit-chart-text"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "numpy>=2.0",
  "opencv-python-headless>=4.10",
  "Pillow>=11.0",
  "paddleocr>=3.0",
]

[project.optional-dependencies]
test = ["pytest>=8.0"]

[project.scripts]
edit-chart-text = "edit_chart_text.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

Create `skills/edit-chart-text/src/edit_chart_text/__init__.py`:

```python
__version__ = "0.1.0"
```

Install editable test dependencies:

```powershell
python -m pip install -e "skills/edit-chart-text[test]"
```

- [ ] **Step 5: Validate the scaffold and run the test**

Run:

```powershell
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/edit-chart-text
python -m pytest skills/edit-chart-text/tests/test_package.py -v
```

Expected: validator reports success and pytest reports `1 passed`.

- [ ] **Step 6: Commit**

```powershell
git add skills/edit-chart-text
git commit -m "chore: scaffold edit chart text skill"
```

### Task 2: Define and validate the replacement request contract

**Files:**
- Create: `skills/edit-chart-text/src/edit_chart_text/models.py`
- Create: `skills/edit-chart-text/src/edit_chart_text/request_io.py`
- Create: `skills/edit-chart-text/tests/test_request_io.py`

- [ ] **Step 1: Write failing request tests**

Create `skills/edit-chart-text/tests/test_request_io.py`:

```python
import pytest

from edit_chart_text.request_io import load_request


def test_loads_dynamic_replacements(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(
        '{"image_path":"chart.png","replacements":['
        '{"old_text":"P10","new_text":"P40","scope":"one"},'
        '{"old_text":"25.00","new_text":"26.50","scope":"all"}]}'
    )
    request = load_request(path)
    assert [(x.old_text, x.new_text) for x in request.replacements] == [
        ("P10", "P40"), ("25.00", "26.50")
    ]


@pytest.mark.parametrize("old,new", [("", "CS"), ("HZ", "")])
def test_rejects_missing_text(tmp_path, old, new):
    path = tmp_path / "request.json"
    path.write_text(
        '{"image_path":"chart.png","replacements":['
        f'{{"old_text":"{old}","new_text":"{new}","scope":"one"}}]}}'
    )
    with pytest.raises(ValueError, match="old_text and new_text"):
        load_request(path)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest skills/edit-chart-text/tests/test_request_io.py -v`

Expected: FAIL with `ModuleNotFoundError: edit_chart_text.request_io`.

- [ ] **Step 3: Implement immutable models**

Create `skills/edit-chart-text/src/edit_chart_text/models.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Scope = Literal["one", "all", "ask"]


@dataclass(frozen=True)
class Replacement:
    old_text: str
    new_text: str
    scope: Scope = "ask"
    location_hint: str | None = None


@dataclass(frozen=True)
class EditRequest:
    image_path: Path
    replacements: tuple[Replacement, ...]


@dataclass(frozen=True)
class TextCandidate:
    text: str
    polygon: tuple[tuple[int, int], ...]
    confidence: float


@dataclass(frozen=True)
class TextStyle:
    color_rgb: tuple[int, int, int]
    font_size: int
    angle_degrees: float = 0.0


@dataclass
class EditReport:
    status: Literal["success", "needs_confirmation", "failed"]
    output_path: str | None = None
    messages: list[str] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Implement strict JSON loading**

Create `skills/edit-chart-text/src/edit_chart_text/request_io.py`:

```python
import json
from pathlib import Path

from .models import EditRequest, Replacement


def load_request(path: str | Path) -> EditRequest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    image_path = Path(data["image_path"])
    replacements = []
    for item in data.get("replacements", []):
        old_text = str(item.get("old_text", "")).strip()
        new_text = str(item.get("new_text", "")).strip()
        if not old_text or not new_text:
            raise ValueError("old_text and new_text must both be non-empty")
        scope = item.get("scope", "ask")
        if scope not in {"one", "all", "ask"}:
            raise ValueError("scope must be one, all, or ask")
        replacements.append(Replacement(old_text, new_text, scope, item.get("location_hint")))
    if not replacements:
        raise ValueError("at least one replacement is required")
    return EditRequest(image_path=image_path, replacements=tuple(replacements))
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest skills/edit-chart-text/tests/test_request_io.py -v`

Expected: `3 passed`.

```powershell
git add skills/edit-chart-text/src/edit_chart_text/models.py skills/edit-chart-text/src/edit_chart_text/request_io.py skills/edit-chart-text/tests/test_request_io.py
git commit -m "feat: define chart text edit requests"
```

### Task 3: Add OCR normalization and safe candidate matching

**Files:**
- Create: `skills/edit-chart-text/src/edit_chart_text/ocr.py`
- Create: `skills/edit-chart-text/src/edit_chart_text/matching.py`
- Create: `skills/edit-chart-text/tests/test_matching.py`

- [ ] **Step 1: Write failing matching tests**

Create `skills/edit-chart-text/tests/test_matching.py`:

```python
from edit_chart_text.matching import choose_candidates
from edit_chart_text.models import Replacement, TextCandidate

CANDIDATES = [
    TextCandidate("HZ", ((10, 10), (30, 10), (30, 20), (10, 20)), 0.96),
    TextCandidate("HZ", ((50, 10), (70, 10), (70, 20), (50, 20)), 0.91),
    TextCandidate("H2", ((80, 10), (100, 10), (100, 20), (80, 20)), 0.88),
]


def test_all_returns_all_exact_matches():
    result = choose_candidates(Replacement("HZ", "CS", "all"), CANDIDATES)
    assert result.status == "ready"
    assert len(result.candidates) == 2


def test_unspecified_multiple_requires_confirmation():
    result = choose_candidates(Replacement("HZ", "CS", "ask"), CANDIDATES)
    assert result.status == "needs_confirmation"


def test_fuzzy_candidate_never_auto_edits():
    result = choose_candidates(Replacement("H7", "CS", "one"), CANDIDATES)
    assert result.status == "needs_confirmation"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest skills/edit-chart-text/tests/test_matching.py -v`

Expected: FAIL because `matching` is absent.

- [ ] **Step 3: Implement matching**

Create `skills/edit-chart-text/src/edit_chart_text/matching.py`:

```python
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Sequence

from .models import Replacement, TextCandidate


@dataclass(frozen=True)
class MatchDecision:
    status: Literal["ready", "needs_confirmation", "not_found"]
    candidates: tuple[TextCandidate, ...]


def choose_candidates(replacement: Replacement, candidates: Sequence[TextCandidate]) -> MatchDecision:
    exact = tuple(c for c in candidates if c.text == replacement.old_text and c.confidence >= 0.80)
    if replacement.scope == "all" and exact:
        return MatchDecision("ready", exact)
    if len(exact) == 1:
        return MatchDecision("ready", exact)
    if len(exact) > 1:
        return MatchDecision("needs_confirmation", exact)
    fuzzy = tuple(
        c for c in candidates
        if SequenceMatcher(None, c.text, replacement.old_text).ratio() >= 0.60
    )
    return MatchDecision("needs_confirmation" if fuzzy else "not_found", fuzzy)
```

- [ ] **Step 4: Implement a lazy PaddleOCR adapter**

Create `skills/edit-chart-text/src/edit_chart_text/ocr.py`:

```python
from pathlib import Path
from typing import Protocol

from PIL import Image

from .models import TextCandidate


class OCRBackend(Protocol):
    def detect(self, image_path: Path) -> list[TextCandidate]: ...


class PaddleOCRBackend:
    def __init__(self) -> None:
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def detect(self, image_path: Path) -> list[TextCandidate]:
        candidates: list[TextCandidate] = []
        with Image.open(image_path) as image:
            scales = (1, 2, 3) if min(image.size) < 1200 else (1, 2)
            for scale in scales:
                scaled = image.resize((image.width * scale, image.height * scale))
                temp = image_path.with_name(f".{image_path.stem}.ocr-{scale}.png")
                scaled.save(temp)
                try:
                    for page in self._ocr.predict(str(temp)):
                        payload = page.json["res"] if hasattr(page, "json") else page["res"]
                        texts = payload.get("rec_texts", [])
                        scores = payload.get("rec_scores", [])
                        polys = payload.get("rec_polys", payload.get("dt_polys", []))
                        for text, score, poly in zip(texts, scores, polys):
                            points = tuple((round(x / scale), round(y / scale)) for x, y in poly)
                            candidates.append(TextCandidate(str(text), points, float(score)))
                finally:
                    temp.unlink(missing_ok=True)
        return _deduplicate(candidates)


def _deduplicate(items: list[TextCandidate]) -> list[TextCandidate]:
    best: dict[tuple[str, tuple[tuple[int, int], ...]], TextCandidate] = {}
    for item in items:
        key = (item.text, item.polygon)
        if key not in best or item.confidence > best[key].confidence:
            best[key] = item
    return list(best.values())
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest skills/edit-chart-text/tests/test_matching.py -v`

Expected: `3 passed`.

```powershell
git add skills/edit-chart-text/src/edit_chart_text/ocr.py skills/edit-chart-text/src/edit_chart_text/matching.py skills/edit-chart-text/tests/test_matching.py
git commit -m "feat: locate chart text candidates"
```

### Task 4: Repair backgrounds and fit replacement text

**Files:**
- Create: `skills/edit-chart-text/src/edit_chart_text/style.py`
- Create: `skills/edit-chart-text/src/edit_chart_text/repair.py`
- Create: `skills/edit-chart-text/src/edit_chart_text/render.py`
- Create: `skills/edit-chart-text/tests/test_image_editing.py`

- [ ] **Step 1: Write synthetic-image tests**

Create `skills/edit-chart-text/tests/test_image_editing.py`:

```python
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from edit_chart_text.models import TextCandidate
from edit_chart_text.repair import repair_region
from edit_chart_text.render import render_replacement
from edit_chart_text.style import estimate_style


def test_repair_and_render_change_only_padded_target(tmp_path):
    image = Image.new("RGB", (180, 80), (28, 121, 177))
    ImageDraw.Draw(image).text((50, 25), "HZ", fill=(20, 30, 40), font=ImageFont.load_default())
    candidate = TextCandidate("HZ", ((48, 23), (68, 23), (68, 39), (48, 39)), 0.99)
    before = np.asarray(image).copy()
    style = estimate_style(image, candidate)
    repaired, allowed = repair_region(image, candidate)
    edited = render_replacement(repaired, candidate, "CS", style)
    after = np.asarray(edited)
    outside = np.ones(before.shape[:2], dtype=bool)
    x0, y0, x1, y1 = allowed
    outside[y0:y1, x0:x1] = False
    assert np.array_equal(before[outside], after[outside])
    assert not np.array_equal(before[y0:y1, x0:x1], after[y0:y1, x0:x1])
```

- [ ] **Step 2: Run the test to verify failure**

Run: `python -m pytest skills/edit-chart-text/tests/test_image_editing.py -v`

Expected: FAIL because style, repair, and render modules are absent.

- [ ] **Step 3: Implement style estimation**

Create `skills/edit-chart-text/src/edit_chart_text/style.py`:

```python
import numpy as np
from PIL import Image

from .models import TextCandidate, TextStyle


def candidate_bounds(candidate: TextCandidate) -> tuple[int, int, int, int]:
    xs = [p[0] for p in candidate.polygon]
    ys = [p[1] for p in candidate.polygon]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def estimate_style(image: Image.Image, candidate: TextCandidate) -> TextStyle:
    x0, y0, x1, y1 = candidate_bounds(candidate)
    crop = np.asarray(image.convert("RGB"))[y0:y1, x0:x1]
    pixels = crop.reshape(-1, 3)
    luminance = pixels.mean(axis=1)
    dark = pixels[luminance <= np.quantile(luminance, 0.25)]
    color = tuple(int(v) for v in np.median(dark if len(dark) else pixels, axis=0))
    return TextStyle(color_rgb=color, font_size=max(8, y1 - y0))
```

- [ ] **Step 4: Implement bounded repair and text rendering**

Create `skills/edit-chart-text/src/edit_chart_text/repair.py`:

```python
import cv2
import numpy as np
from PIL import Image

from .models import TextCandidate
from .style import candidate_bounds


def repair_region(image: Image.Image, candidate: TextCandidate, padding: int = 2):
    rgb = np.asarray(image.convert("RGB")).copy()
    x0, y0, x1, y1 = candidate_bounds(candidate)
    x0, y0 = max(0, x0 - padding), max(0, y0 - padding)
    x1, y1 = min(image.width, x1 + padding), min(image.height, y1 + padding)
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    mask[y0:y1, x0:x1] = 255
    repaired = cv2.inpaint(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), mask, 2, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)), (x0, y0, x1, y1)
```

Create `skills/edit-chart-text/src/edit_chart_text/render.py`:

```python
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import TextCandidate, TextStyle
from .style import candidate_bounds


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def render_replacement(image: Image.Image, candidate: TextCandidate, text: str, style: TextStyle) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result)
    x0, y0, x1, y1 = candidate_bounds(candidate)
    size = style.font_size
    while size >= 6:
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= (x1 - x0) * 1.20:
            draw.text((x0, y0), text, font=font, fill=style.color_rgb)
            return result
        size -= 1
    raise ValueError("replacement text does not fit safely")
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest skills/edit-chart-text/tests/test_image_editing.py -v`

Expected: `1 passed`.

```powershell
git add skills/edit-chart-text/src/edit_chart_text/style.py skills/edit-chart-text/src/edit_chart_text/repair.py skills/edit-chart-text/src/edit_chart_text/render.py skills/edit-chart-text/tests/test_image_editing.py
git commit -m "feat: repair and redraw chart text"
```

### Task 5: Add validation, reports, and safe orchestration

**Files:**
- Create: `skills/edit-chart-text/src/edit_chart_text/validate.py`
- Create: `skills/edit-chart-text/src/edit_chart_text/pipeline.py`
- Create: `skills/edit-chart-text/tests/test_pipeline.py`

- [ ] **Step 1: Write failing orchestration tests**

Create `skills/edit-chart-text/tests/test_pipeline.py`:

```python
import json
from pathlib import Path

from PIL import Image

from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


class FakeOCR:
    def detect(self, image_path: Path):
        return [TextCandidate("HZ", ((10, 10), (30, 10), (30, 24), (10, 24)), 0.99)]


def test_pipeline_never_overwrites_source(tmp_path):
    source = tmp_path / "chart.png"
    Image.new("RGB", (100, 60), "steelblue").save(source)
    original = source.read_bytes()
    report = run_pipeline(EditRequest(source, (Replacement("HZ", "CS", "one"),)), FakeOCR())
    assert report.status == "success"
    assert source.read_bytes() == original
    assert (tmp_path / "chart_edited.png").exists()
    assert json.loads((tmp_path / "chart_edit-report.json").read_text())["status"] == "success"


class AmbiguousOCR:
    def detect(self, image_path: Path):
        return [
            TextCandidate("HZ", ((10, 10), (30, 10), (30, 24), (10, 24)), 0.99),
            TextCandidate("HZ", ((40, 10), (60, 10), (60, 24), (40, 24)), 0.98),
        ]


def test_ambiguous_request_produces_no_edited_image(tmp_path):
    source = tmp_path / "chart.png"
    Image.new("RGB", (100, 60), "steelblue").save(source)
    report = run_pipeline(EditRequest(source, (Replacement("HZ", "CS", "ask"),)), AmbiguousOCR())
    assert report.status == "needs_confirmation"
    assert not (tmp_path / "chart_edited.png").exists()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest skills/edit-chart-text/tests/test_pipeline.py -v`

Expected: FAIL because `pipeline` is absent.

- [ ] **Step 3: Implement boundary validation**

Create `skills/edit-chart-text/src/edit_chart_text/validate.py`:

```python
import numpy as np
from PIL import Image


def unchanged_outside(before: Image.Image, after: Image.Image, boxes: list[tuple[int, int, int, int]]) -> bool:
    a = np.asarray(before.convert("RGB"))
    b = np.asarray(after.convert("RGB"))
    allowed = np.zeros(a.shape[:2], dtype=bool)
    for x0, y0, x1, y1 in boxes:
        allowed[y0:y1, x0:x1] = True
    return bool(np.array_equal(a[~allowed], b[~allowed]))
```

- [ ] **Step 4: Implement orchestration and atomic outputs**

Create `skills/edit-chart-text/src/edit_chart_text/pipeline.py` with `run_pipeline(request, ocr_backend)` that:

```python
import json
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw

from .matching import choose_candidates
from .models import EditReport, EditRequest
from .repair import repair_region
from .render import render_replacement
from .style import estimate_style
from .validate import unchanged_outside


def run_pipeline(request: EditRequest, ocr_backend) -> EditReport:
    source = request.image_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    original = Image.open(source).convert("RGB")
    candidates = ocr_backend.detect(source)
    decisions = [(item, choose_candidates(item, candidates)) for item in request.replacements]
    if any(decision.status != "ready" for _, decision in decisions):
        report = EditReport("needs_confirmation", messages=["Target is missing or ambiguous."])
        _write_report(source, report)
        _write_candidates(source, original, decisions)
        return report

    edited = original.copy()
    allowed_boxes = []
    report = EditReport("success")
    for replacement, decision in decisions:
        for candidate in decision.candidates:
            style = estimate_style(edited, candidate)
            edited, allowed = repair_region(edited, candidate)
            edited = render_replacement(edited, candidate, replacement.new_text, style)
            allowed_boxes.append(allowed)
            report.edits.append({"old_text": replacement.old_text, "new_text": replacement.new_text, "polygon": candidate.polygon, "confidence": candidate.confidence})
    if not unchanged_outside(original, edited, allowed_boxes):
        report.status = "failed"
        report.messages.append("Pixels changed outside approved edit regions.")
        _write_report(source, report)
        return report
    output = source.with_name(f"{source.stem}_edited.png")
    temp = output.with_suffix(".tmp.png")
    edited.save(temp)
    temp.replace(output)
    report.output_path = str(output)
    _write_report(source, report)
    return report


def _write_report(source: Path, report: EditReport) -> None:
    path = source.with_name(f"{source.stem}_edit-report.json")
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_candidates(source: Path, image: Image.Image, decisions) -> None:
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    number = 1
    for _, decision in decisions:
        for candidate in decision.candidates:
            draw.line(candidate.polygon + (candidate.polygon[0],), fill="red", width=2)
            draw.text(candidate.polygon[0], str(number), fill="red")
            number += 1
    preview.save(source.with_name(f"{source.stem}_candidates.png"))
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest skills/edit-chart-text/tests/test_pipeline.py -v`

Expected: `2 passed`.

```powershell
git add skills/edit-chart-text/src/edit_chart_text/validate.py skills/edit-chart-text/src/edit_chart_text/pipeline.py skills/edit-chart-text/tests/test_pipeline.py
git commit -m "feat: orchestrate safe chart text edits"
```

### Task 6: Add the CLI and user-facing Skill workflow

**Files:**
- Create: `skills/edit-chart-text/src/edit_chart_text/cli.py`
- Modify: `skills/edit-chart-text/SKILL.md`
- Create: `skills/edit-chart-text/references/troubleshooting.md`
- Create: `skills/edit-chart-text/tests/test_cli.py`

- [ ] **Step 1: Write a failing CLI test**

Create `skills/edit-chart-text/tests/test_cli.py`:

```python
from edit_chart_text.cli import main


def test_missing_request_returns_usage_error():
    assert main([]) == 2
```

- [ ] **Step 2: Run the test to verify failure**

Run: `python -m pytest skills/edit-chart-text/tests/test_cli.py -v`

Expected: FAIL because `cli` is absent.

- [ ] **Step 3: Implement the CLI**

Create `skills/edit-chart-text/src/edit_chart_text/cli.py`:

```python
import argparse

from .ocr import PaddleOCRBackend
from .pipeline import run_pipeline
from .request_io import load_request


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="edit-chart-text")
    parser.add_argument("--request", required=True, help="UTF-8 JSON request file")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        report = run_pipeline(load_request(args.request), PaddleOCRBackend())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2
    return {"success": 0, "needs_confirmation": 3, "failed": 4}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Replace the scaffolded SKILL.md**

Write `skills/edit-chart-text/SKILL.md` with only `name` and `description` in frontmatter. The body must instruct Codex to:

```markdown
---
name: edit-chart-text
description: Replace user-specified text or numbers in scientific charts, maps, and engineering diagram images while preserving surrounding pixels. Use when a user uploads a PNG/JPG/JPEG and asks in natural language to change one or more old text values into new values, including requests to replace one occurrence or all occurrences.
---

# Edit Chart Text

1. Read the user's current request; never reuse example values from prior turns.
2. Extract each `old_text`, `new_text`, `scope`, and optional `location_hint`.
3. Ask one concise question if either text value is missing.
4. Write a UTF-8 JSON request file beside the input image. Never overwrite the input image.
5. Run `edit-chart-text --request <request.json>`.
6. Interpret exit code 0 as success, 3 as ambiguity, and 4 as failed validation.
7. On ambiguity, show `<stem>_candidates.png`, report how many candidates were found, and ask whether to modify one numbered candidate or all.
8. On success, show `<stem>_edited.png` and link the JSON report.
9. On failure, keep the original untouched and consult [references/troubleshooting.md](references/troubleshooting.md).

Use `scope: "all"` only when the user explicitly says all/every occurrence. Use `scope: "ask"` when multiple occurrences are possible and the user did not specify a scope. Fuzzy OCR matches are candidates for confirmation only and must never be edited automatically.
```

- [ ] **Step 5: Add concise troubleshooting guidance**

Create `skills/edit-chart-text/references/troubleshooting.md`:

```markdown
# Troubleshooting

| Report message | Meaning | Next action |
|---|---|---|
| `not found` | OCR found no exact target text. | Show the candidate preview and ask the user for the target's approximate position. |
| `multiple matches` | More than one exact target exists and scope is not `all`. | Ask the user for a candidate number or permission to replace all. |
| `replacement text does not fit safely` | The new text would overlap protected chart content. | Ask whether to use a smaller font or cancel the edit. |
| `pixels changed outside approved edit regions` | Validation detected an unsafe edit. | Keep the original, show the diagnostic report, and do not present an edited image as successful. |
| OCR model unavailable | PaddleOCR cannot load its local model. | Explain that the first run must download the model once, then retry offline after the model is cached. |
```

- [ ] **Step 6: Validate, test, and commit**

Run:

```powershell
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/edit-chart-text
python -m pytest skills/edit-chart-text/tests/test_cli.py -v
```

Expected: Skill validation succeeds and `1 passed`.

```powershell
git add skills/edit-chart-text/SKILL.md skills/edit-chart-text/references/troubleshooting.md skills/edit-chart-text/src/edit_chart_text/cli.py skills/edit-chart-text/tests/test_cli.py
git commit -m "feat: add chart text editing workflow"
```

### Task 7: Run the full suite and validate the uploaded example

**Files:**
- Create: `skills/edit-chart-text/tests/fixtures/chart_sample.png`
- Create: `skills/edit-chart-text/tests/fixtures/chart_sample_request.json`
- Create: `skills/edit-chart-text/tests/test_real_fixture.py`

- [ ] **Step 1: Copy the uploaded image into the fixture directory**

Copy `C:\Users\飞翔无限\AppData\Local\Temp\codex-clipboard-35a2dfa6-fec8-4495-bec7-af5beef9dfd3.png` to `skills/edit-chart-text/tests/fixtures/chart_sample.png` without altering it.

- [ ] **Step 2: Create the dynamic request fixture**

Create `skills/edit-chart-text/tests/fixtures/chart_sample_request.json`:

```json
{
  "image_path": "skills/edit-chart-text/tests/fixtures/chart_sample.png",
  "replacements": [
    {"old_text": "HZ", "new_text": "CS", "scope": "ask"}
  ]
}
```

- [ ] **Step 3: Add a non-overwrite real-fixture test**

Create `skills/edit-chart-text/tests/test_real_fixture.py`:

```python
from pathlib import Path

from edit_chart_text.ocr import PaddleOCRBackend
from edit_chart_text.pipeline import run_pipeline
from edit_chart_text.request_io import load_request


def test_uploaded_chart_is_never_overwritten():
    request_path = Path("skills/edit-chart-text/tests/fixtures/chart_sample_request.json")
    request = load_request(request_path)
    before = request.image_path.read_bytes()
    report = run_pipeline(request, PaddleOCRBackend())
    assert request.image_path.read_bytes() == before
    assert report.status in {"success", "needs_confirmation"}
```

- [ ] **Step 4: Run the complete suite**

Run:

```powershell
python -m pytest skills/edit-chart-text/tests -v
```

Expected: all tests pass; the real fixture returns either a validated edit or an explicit candidate-confirmation state, never an unvalidated success.

- [ ] **Step 5: Run the real CLI and visually inspect outputs**

Run:

```powershell
edit-chart-text --request skills/edit-chart-text/tests/fixtures/chart_sample_request.json
```

Expected: exit `0` with `chart_sample_edited.png`, or exit `3` with `chart_sample_candidates.png`; `chart_sample.png` remains byte-identical.

Inspect the produced image at 100% and 400% zoom. Reject the result if a rectangular patch, altered point marker, broken line, or change outside the target region is visible. Convert every observed defect into a focused regression test before changing implementation.

- [ ] **Step 6: Re-run Skill validation and commit**

```powershell
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills/edit-chart-text
python -m pytest skills/edit-chart-text/tests -v
git add skills/edit-chart-text
git commit -m "test: validate chart text skill on sample image"
```

### Task 8: Install the validated Skill locally

**Files:**
- Copy validated folder: `skills/edit-chart-text/`
- Destination after approval: `C:\Users\飞翔无限\.codex\skills\edit-chart-text\`

- [ ] **Step 1: Confirm installation authority**

Ask the user before writing outside the workspace. Do not install if approval is withheld; the version-controlled Skill remains usable as the source artifact.

- [ ] **Step 2: Copy only runtime Skill files**

Copy `SKILL.md`, `agents/`, `src/`, `references/`, `assets/`, and `pyproject.toml`. Exclude `tests/`, caches, generated outputs, and temporary request files.

- [ ] **Step 3: Validate the installed copy**

Run:

```powershell
python C:\Users\飞翔无限\.codex\skills\.system\skill-creator\scripts\quick_validate.py C:\Users\飞翔无限\.codex\skills\edit-chart-text
```

Expected: validation succeeds.

- [ ] **Step 4: Final verification**

Start a fresh Codex task and issue: “用 edit-chart-text 把测试图片中的 HZ 修改成 CS。” Confirm that the Skill triggers, dynamically extracts the two strings, preserves the original, and returns either a validated edited image or a candidate-confirmation prompt.

