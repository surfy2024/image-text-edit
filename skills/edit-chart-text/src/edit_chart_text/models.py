"""Data models shared by the chart text editing pipeline."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


Scope = Literal["one", "all", "ask"]
ReportStatus = Literal["success", "needs_confirmation", "failed"]
Polygon = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Replacement:
    old_text: str
    new_text: str
    scope: Scope = "ask"
    location_hint: str | None = None
    candidate_number: int | None = None


@dataclass(frozen=True)
class EditRequest:
    image_path: Path
    replacements: tuple[Replacement, ...]


@dataclass(frozen=True)
class TextCandidate:
    text: str
    polygon: Polygon
    confidence: float


@dataclass(frozen=True)
class TextStyle:
    color_rgb: tuple[int, int, int]
    font_size: int
    angle_degrees: float = 0.0


@dataclass
class EditReport:
    status: ReportStatus
    output_path: str | None = None
    messages: list[str] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
