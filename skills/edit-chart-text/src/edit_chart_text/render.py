"""Safely redraw replacement text within an explicitly allowed region."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from .models import TextCandidate, TextStyle
from .style import candidate_bounds


def _font(size: int, text: str):
    cjk = any(ord(c) > 0x2E7F for c in text)
    names = (["msyh.ttc", "simsun.ttc", "arial.ttf"] if cjk else ["arial.ttf", "msyh.ttc", "simsun.ttc"])
    roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            try:
                return ImageFont.truetype(str(root / name), size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def render_text(image: Image.Image, text: str, style: TextStyle, target_bounds, allowed_bounds):
    w, h = image.size
    al, at, ar, ab = (int(v) for v in allowed_bounds)
    if not (0 <= al < ar <= w and 0 <= at < ab <= h):
        raise ValueError("invalid allowed box")
    tl, tt, tr, tb = (int(v) for v in target_bounds)
    if not (0 <= tl < tr <= w and 0 <= tt < tb <= h):
        raise ValueError("invalid target box")
    if not (al <= tl and at <= tt and tr <= ar and tb <= ab):
        raise ValueError("invalid target box: target must be contained in allowed box")
    chosen = None
    for size in range(max(6, int(style.font_size)), 5, -1):
        font = _font(size, text)
        bbox = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        if tw <= ar-al and th <= ab-at:
            chosen = font, tw, th, bbox; break
    if chosen is None:
        raise ValueError("replacement text does not fit safely")
    font, tw, th, bbox = chosen
    x = min(max(tl, al), ar-tw)
    y = min(max(tt, at), ab-th) - bbox[1]
    out = image.convert("RGB").copy()
    ImageDraw.Draw(out).text((x, y), text, font=font, fill=style.color_rgb)
    return out


def render_replacement(
    image: Image.Image,
    candidate: TextCandidate,
    text: str,
    style: TextStyle,
    allowed_bounds=None,
) -> Image.Image:
    """Render a replacement through the stable pipeline-facing API."""
    target = candidate_bounds(candidate, image.size)
    allowed = target if allowed_bounds is None else allowed_bounds
    return render_text(image, text, style, target, allowed)
