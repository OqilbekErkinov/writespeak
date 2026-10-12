"""Removes the @cdireport channel mark from Writing Task 1 charts.

1. Find it: RapidOCR (local ONNX models) reads every line of text in the
   image and the lines that spell the channel name are kept ("@cdireport",
   "@CDIREPORT", "t.me/CDI_Report", tolerating OCR slips). When nothing
   matches, the saturated pixels alone are read again, upright and turned,
   which catches slanted marks over a busy chart.
2. Mask it, per found line:
   - coloured text (the usual orange or red): only pixels of that colour are
     masked; dark pixels of that colour - chart lines and labels showing
     through a semi-transparent mark - are turned grey instead of erased;
     a light tinted box behind the text is masked whole;
   - white text on a coloured box: the box;
   - grey/black text: its dark pixels;
   - a Telegram logo just left of the text goes too.
3. Fill it: LaMa inpainting (ONNX, CPU) on a crop around each mask; only the
   masked pixels of the result are pasted back, so the rest of the chart
   stays pixel-for-pixel the original.

Other publishers' logos are left alone - only the channel name is removed.
The LaMa model (208 MB) is downloaded once into data/models/.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.config import settings

logger = logging.getLogger(__name__)
logging.getLogger("RapidOCR").setLevel(logging.ERROR)

LAMA_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
LAMA_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
LAMA_SIZE = 512

# The channel name as OCR may read it: c-d-i-report, with an optional @ / t.me/,
# i read as l/1/|, o as 0, and a space or underscore after "cdi".
_MARK_RX = re.compile(r"c\s*d\s*[il1|!]\s*[_\s]?\s*r\s*e\s*p\s*[o0]\s*r\s*t", re.I)
SECOND_PASS_ANGLES = (0, 30, -30, 45, -45)
MAX_MASK_SHARE = 0.3  # a bigger mask means a wrong detection - leave the image alone

_lock = threading.Lock()
_ocr = None
_lama = None


def _models_dir() -> Path:
    path = Path(settings.file_storage_path).resolve().parent / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _get_lama():
    global _lama
    if _lama is None:
        import onnxruntime as ort

        path = _models_dir() / "lama_fp32.onnx"
        if not path.exists():
            logger.info("Downloading the LaMa inpainting model to %s", path)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(LAMA_URL, tmp)
            digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
            if digest != LAMA_SHA256:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"LaMa model checksum mismatch: {digest}")
            tmp.rename(path)
        _lama = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _lama


@dataclass
class Found:
    quad: np.ndarray  # 4x2 float32, clockwise from top-left of the text
    text: str


def _match_span(text: str) -> tuple[float, float] | None:
    """Where the channel name sits in an OCR line, as fractions of its length."""
    match = _MARK_RX.search(text)
    if match is None:
        return None
    start, end = match.start(), match.end()
    # Take the "@" / "t.me/" / "https://" in front along with it.
    prefix = re.search(r"(https?://)?(t\.me/|@)?\s*$", text[:start], re.I)
    if prefix:
        start = prefix.start()
    return start / len(text), end / len(text)


def _shrink(quad: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """The part of a text line's quad between fractions t0..t1 of its length."""
    if t0 <= 0.02 and t1 >= 0.98:
        return quad
    p0, p1, p2, p3 = quad
    t0, t1 = max(0.0, t0 - 0.02), min(1.0, t1 + 0.02)
    return np.array(
        [p0 + (p1 - p0) * t0, p0 + (p1 - p0) * t1, p3 + (p2 - p3) * t1, p3 + (p2 - p3) * t0], np.float32
    )


def _read_marks(ocr, image: np.ndarray) -> list[Found]:
    result = ocr(image)
    found = []
    for box, text in zip(result.boxes if result.txts else [], result.txts or []):
        span = _match_span(text)
        if span is not None:
            found.append(Found(_shrink(np.array(box, np.float32), *span), text))
    return found


def find_marks(image: np.ndarray) -> list[Found]:
    ocr = _get_ocr()
    found = _read_marks(ocr, image)
    if found:
        return found
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturated = (hsv[..., 1] >= 45) & (hsv[..., 2] >= 90)
    if saturated.sum() < 0.002 * saturated.size:
        return []
    ink = np.full(image.shape[:2], 255, np.uint8)
    ink[saturated] = 0
    ink = cv2.cvtColor(ink, cv2.COLOR_GRAY2BGR)
    height, width = ink.shape[:2]
    centre = (width / 2, height / 2)
    for angle in SECOND_PASS_ANGLES:
        matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
        cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
        new_w, new_h = int(height * sin + width * cos), int(height * cos + width * sin)
        matrix[0, 2] += new_w / 2 - centre[0]
        matrix[1, 2] += new_h / 2 - centre[1]
        turned = cv2.warpAffine(ink, matrix, (new_w, new_h), borderValue=(255, 255, 255))
        back = cv2.invertAffineTransform(matrix)
        found = [
            Found(((back[:, :2] @ f.quad.T).T + back[:, 2]).astype(np.float32), f.text)
            for f in _read_marks(ocr, turned)
        ]
        if found:
            return found
    return []


def _box_rect(sat, inside_s: float, x0: int, y0: int, x1: int, y1: int, text_h: float):
    """Bounds of a light tinted box behind the text, or None if there is none."""
    height, width = sat.shape
    reach = int(1.6 * text_h) + 2
    outside = np.r_[
        sat[max(0, y0 - reach - 4) : max(0, y0 - reach), x0:x1].ravel(),
        sat[min(height, y1 + reach) : min(height, y1 + reach + 4), x0:x1].ravel(),
        [0],
    ]
    outside_s = float(np.median(outside))
    if inside_s < max(5.0, outside_s + 3):
        return None
    threshold = (inside_s + outside_s) / 2

    def row(y):
        return np.median(sat[y, x0:x1]) if 0 <= y < height else 0

    def col(x):
        return np.median(sat[y0:y1, x]) if 0 <= x < width else 0

    top, bottom, left, right = y0, y1, x0, x1
    while top > max(0, y0 - reach) and row(top - 1) >= threshold:
        top -= 1
    while bottom < min(height, y1 + reach) and row(bottom) >= threshold:
        bottom += 1
    while left > max(0, x0 - reach) and col(left - 1) >= threshold:
        left -= 1
    while right < min(width, x1 + reach) and col(right) >= threshold:
        right += 1
    return top, bottom, left, right


def _mark_mask(image: np.ndarray, hsv: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(inpaint mask, pixels to turn grey) for one found line."""
    height, width = image.shape[:2]
    hue, sat, val = (hsv[..., i].astype(np.int16) for i in range(3))
    edge1, edge2 = np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[1])
    text_h = max(4.0, min(edge1, edge2))
    centre = quad.mean(axis=0)
    outward = quad - centre
    outward /= np.linalg.norm(outward, axis=1, keepdims=True) + 1e-6
    poly = quad + outward * 0.3 * text_h
    region = np.zeros((height, width), np.uint8)
    cv2.fillPoly(region, [poly.astype(np.int32)], 255)
    inside = region > 0
    mask = np.zeros((height, width), np.uint8)
    grey = np.zeros((height, width), bool)
    if not inside.any():
        return mask, grey

    colourful = (sat >= 40) & (val >= 90)
    share = colourful[inside].mean()
    x0, y0 = int(max(0, poly[:, 0].min())), int(max(0, poly[:, 1].min()))
    x1, y1 = int(min(width, poly[:, 0].max())), int(min(height, poly[:, 1].max()))

    if share > 0.45:
        # Light text on a coloured box: mask the box it sits on - pixels of the
        # box's own colour (a warm-lit photo of paper is "colourful" all over),
        # never reaching further than a little beyond the text.
        hist = np.bincount(hue[inside & colourful], minlength=180).astype(float)
        dominant = int(np.argmax(np.convolve(np.r_[hist[-5:], hist, hist[:5]], np.ones(11), "valid")))
        distance = np.minimum(np.abs(hue - dominant), 180 - np.abs(hue - dominant))
        box_s = float(np.median(sat[inside & colourful]))
        boxlike = (distance <= 10) & (sat >= max(60.0, 0.6 * box_s)) & (val >= 90)
        reach = int(1.5 * text_h)
        bx0, by0 = max(0, x0 - reach), max(0, y0 - reach)
        bx1, by1 = min(width, x1 + reach), min(height, y1 + reach)
        count, labels = cv2.connectedComponents(boxlike.astype(np.uint8))
        for label in set(np.unique(labels[inside & boxlike])) - {0}:
            ys, xs = np.nonzero(labels == label)
            if len(xs) > 0.2 * inside.sum():
                mask[max(by0, ys.min()) : min(by1, ys.max() + 1), max(bx0, xs.min()) : min(bx1, xs.max() + 1)] = 255
        mask[inside] = 255
    elif share > 0.02:
        # Coloured text: its dominant hue (circular, so red wraps around 0/180).
        hist = np.bincount(hue[inside & colourful], minlength=180).astype(float)
        dominant = int(np.argmax(np.convolve(np.r_[hist[-5:], hist, hist[:5]], np.ones(11), "valid")))
        distance = np.minimum(np.abs(hue - dominant), 180 - np.abs(hue - dominant))
        coloured = inside & (distance <= 14) & (sat >= 12) & (val >= 60)
        dark = coloured & (val < 170)
        grey |= dark
        mask[coloured & ~dark] = 255
        level = abs(quad[1][1] - quad[0][1]) < 0.2 * max(edge1, edge2)
        text_px = cv2.dilate(coloured.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        background = inside & ~text_px & (val >= 200)
        if level and background.sum() > 30:
            box = _box_rect(sat, float(np.median(sat[background])), x0, y0, x1, y1, text_h)
            if box is not None:
                top, bottom, left, right = box
                mask[top:bottom, left:right] = 255
                grey[top:bottom, left:right] = False
    else:
        mask[inside & ((val < 215) | (sat >= 25))] = 255

    # A Telegram logo (blue circle) just left of the text.
    if edge1 >= edge2:
        along = (quad[1] - quad[0]) / max(edge1, 1e-6)
        left_poly = np.array(
            [quad[0] - along * 1.8 * text_h, quad[0], quad[3], quad[3] - along * 1.8 * text_h], np.float32
        )
        left_poly += (left_poly - left_poly.mean(axis=0)) * 0.3
        left_region = np.zeros((height, width), np.uint8)
        cv2.fillPoly(left_region, [left_poly.astype(np.int32)], 255)
        blue = (hue >= 90) & (hue <= 120) & (sat >= 60) & (left_region > 0)
        if blue.sum() > 0.15 * text_h * text_h:
            ys, xs = np.nonzero(blue)
            radius = int(0.62 * max(np.ptp(xs), np.ptp(ys))) + 4
            cv2.circle(mask, (int(xs.mean()), int(ys.mean())), radius, 255, -1)

    size = max(3, int(0.12 * text_h) | 1)
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    return mask, grey


def _inpaint(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """LaMa on a crop around each separate masked area; pastes back masked pixels only."""
    lama = _get_lama()
    height, width = image.shape[:2]
    out = image.copy()
    joined = cv2.dilate(mask, np.ones((41, 41), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((joined > 0).astype(np.uint8))
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        part = (labels == label) & (mask > 0)
        if not part.any():
            continue
        ys, xs = np.nonzero(part)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        side = max(160, int(max(x1 - x0, y1 - y0) * 1.5) + 64)
        crop_w, crop_h = min(side, width), min(side, height)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        ax = int(np.clip(cx - crop_w // 2, 0, width - crop_w))
        ay = int(np.clip(cy - crop_h // 2, 0, height - crop_h))
        crop = out[ay : ay + crop_h, ax : ax + crop_w]
        crop_mask = part[ay : ay + crop_h, ax : ax + crop_w]

        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        small_mask = cv2.resize(crop_mask.astype(np.uint8), (LAMA_SIZE, LAMA_SIZE), interpolation=cv2.INTER_NEAREST)
        small_mask = cv2.dilate(small_mask, np.ones((3, 3), np.uint8)).astype(np.float32)
        result = lama.run(
            None, {"image": rgb.transpose(2, 0, 1)[None], "mask": small_mask[None, None]}
        )[0][0].transpose(1, 2, 0)
        if result.max() <= 1.5:
            result = result * 255
        result = cv2.cvtColor(np.clip(result, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        result = cv2.resize(result, (crop_w, crop_h), interpolation=cv2.INTER_CUBIC)
        crop[crop_mask] = result[crop_mask]
    return out


def remove_watermark(content: bytes) -> tuple[bytes, int]:
    """(image, number of channel marks removed). With none found - or an
    unreadable image - the original bytes come back unchanged. CPU-bound:
    call it via asyncio.to_thread."""
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return content, 0
    with _lock:
        found = find_marks(image)
        if not found:
            return content, 0
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = np.zeros(image.shape[:2], np.uint8)
        grey = np.zeros(image.shape[:2], bool)
        for mark in found:
            part_mask, part_grey = _mark_mask(image, hsv, mark.quad)
            mask |= part_mask
            grey |= part_grey
        if mask.mean() / 255 > MAX_MASK_SHARE:
            logger.warning("Watermark mask covers %.0f%% of the image - left as is", mask.mean() / 2.55)
            return content, 0
        cleaned = image.copy()
        if grey.any():
            level = cleaned[grey].min(axis=1)
            cleaned[grey] = np.stack([level, level, level], axis=1)
        if mask.any():
            cleaned = _inpaint(cleaned, mask)
    ok, png = cv2.imencode(".png", cleaned)
    if not ok:
        return content, 0
    logger.info("Removed %d channel mark(s): %s", len(found), ", ".join(m.text for m in found))
    return png.tobytes(), len(found)
