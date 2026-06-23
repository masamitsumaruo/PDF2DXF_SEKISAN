# -*- coding: utf-8 -*-
"""PDF図面を実寸mmのDXFへ変換する小型アプリ。

使い方:
    python pdf2dxf.py                 # GUIを起動
    python pdf2dxf.py input.pdf -o out.dxf

前提:
    ベクターPDFを対象にします。スキャン画像だけのPDFは線分や寸法値を
    抽出できないため、正確なDXF化はできません。
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import ezdxf
import fitz


DIMENSION_RE = re.compile(
    r"^[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:mm|MM|ｍｍ))?$"
)
MIN_DIMENSION_VALUE_MM = 50.0
MIN_SCALE_MM_PER_PT = 5.0
MAX_SCALE_MM_PER_PT = 500.0

FULLWIDTH_TRANS = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "，": ",",
        "．": ".",
        "－": "-",
        "＋": "+",
        "　": " ",
    }
)


@dataclass(frozen=True)
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def dx(self) -> float:
        return self.x2 - self.x1

    @property
    def dy(self) -> float:
        return self.y2 - self.y1

    @property
    def length(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def orientation(self) -> str | None:
        if self.length < 1.0:
            return None
        if abs(self.dy) <= max(0.08, abs(self.dx) * 0.01):
            return "h"
        if abs(self.dx) <= max(0.08, abs(self.dy) * 0.01):
            return "v"
        return None


@dataclass(frozen=True)
class TextSpan:
    text: str
    value: float
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    direction: tuple[float, float]

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def orientation(self) -> str:
        dx, dy = self.direction
        if abs(dy) > abs(dx):
            return "v"
        if self.height > self.width * 1.35:
            return "v"
        return "h"


def transform_point(point: fitz.Point, matrix: fitz.Matrix | None) -> fitz.Point:
    if matrix is None:
        return point
    return point * matrix


def transform_vector(vector: tuple[float, float], matrix: fitz.Matrix | None) -> tuple[float, float]:
    if matrix is None:
        return vector
    x, y = vector
    return (x * matrix.a + y * matrix.c, x * matrix.b + y * matrix.d)


def transform_segment(segment: Segment, matrix: fitz.Matrix | None) -> Segment:
    if matrix is None:
        return segment
    p1 = fitz.Point(segment.x1, segment.y1) * matrix
    p2 = fitz.Point(segment.x2, segment.y2) * matrix
    return Segment(p1.x, p1.y, p2.x, p2.y)


def transform_text_span(span: TextSpan, matrix: fitz.Matrix | None) -> TextSpan:
    if matrix is None:
        return span
    x0, y0, x1, y1 = span.bbox
    corners = [
        fitz.Point(x0, y0) * matrix,
        fitz.Point(x1, y0) * matrix,
        fitz.Point(x1, y1) * matrix,
        fitz.Point(x0, y1) * matrix,
    ]
    xs = [p.x for p in corners]
    ys = [p.y for p in corners]
    origin = fitz.Point(span.origin[0], span.origin[1]) * matrix
    direction = transform_vector(span.direction, matrix)
    return TextSpan(
        text=span.text,
        value=span.value,
        bbox=(min(xs), min(ys), max(xs), max(ys)),
        origin=(origin.x, origin.y),
        size=span.size,
        direction=direction,
    )


@dataclass(frozen=True)
class ScaleMatch:
    text: str
    value: float
    line_length_pt: float
    scale_mm_per_pt: float
    orientation: str
    axis_start_pt: float
    axis_end_pt: float
    line_x1_pt: float
    line_y1_pt: float
    line_x2_pt: float
    line_y2_pt: float
    text_size_pt: float


@dataclass(frozen=True)
class AxisCalibrator:
    anchors: tuple[tuple[float, float], ...]
    default_scale: float

    @classmethod
    def linear(cls, scale: float) -> "AxisCalibrator":
        return cls(anchors=tuple(), default_scale=scale)

    @classmethod
    def from_intervals(
        cls,
        intervals: Iterable[tuple[float, float, float]],
        default_scale: float,
    ) -> "AxisCalibrator":
        normalized = sorted(
            (min(a, b), max(a, b), abs(value))
            for a, b, value in intervals
            if abs(a - b) > 0.001 and abs(value) > 0.001
        )
        if not normalized:
            return cls.linear(default_scale)

        previous_end: float | None = None
        for start, end, _value in normalized:
            if previous_end is not None and start < previous_end - 0.5:
                return cls.linear(default_scale)
            previous_end = max(previous_end or end, end)

        anchors: list[tuple[float, float]] = []
        current_pdf: float | None = None
        current_mm: float | None = None

        for start, end, value in normalized:
            if current_pdf is None or current_mm is None:
                start_mm = start * default_scale
            elif abs(start - current_pdf) <= 0.5:
                start = current_pdf
                start_mm = current_mm
            else:
                start_mm = current_mm + (start - current_pdf) * default_scale

            end_mm = start_mm + value
            if not anchors or abs(anchors[-1][0] - start) > 0.5:
                anchors.append((start, start_mm))
            else:
                anchors[-1] = (anchors[-1][0], start_mm)
            anchors.append((end, end_mm))
            current_pdf = end
            current_mm = end_mm

        deduped: list[tuple[float, float]] = []
        for pdf_coord, mm_coord in anchors:
            if deduped and abs(deduped[-1][0] - pdf_coord) <= 0.5:
                deduped[-1] = (deduped[-1][0], mm_coord)
            else:
                deduped.append((pdf_coord, mm_coord))
        return cls(anchors=tuple(deduped), default_scale=default_scale)

    def map(self, coord: float) -> float:
        if not self.anchors:
            return coord * self.default_scale
        anchors = self.anchors
        if coord <= anchors[0][0]:
            a_pdf, a_mm = anchors[0]
            return a_mm + (coord - a_pdf) * self.default_scale
        if coord >= anchors[-1][0]:
            a_pdf, a_mm = anchors[-1]
            return a_mm + (coord - a_pdf) * self.default_scale
        for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
            if x0 <= coord <= x1:
                if abs(x1 - x0) <= 0.001:
                    return y0
                t = (coord - x0) / (x1 - x0)
                return y0 + (y1 - y0) * t
        return coord * self.default_scale


@dataclass(frozen=True)
class ConversionResult:
    input_pdf: Path
    output_dxf: Path
    page_number: int
    scale_mm_per_pt: float
    scale_x_mm_per_pt: float
    scale_y_mm_per_pt: float
    scale_source: str
    line_count: int
    curve_count: int
    rect_count: int
    dimension_text_count: int
    label_text_count: int
    scale_matches: tuple[ScaleMatch, ...]
    extents: tuple[float, float, float, float] | None


def normalize_text(text: str) -> str:
    return text.translate(FULLWIDTH_TRANS).strip()


def parse_dimension_value(text: str) -> float | None:
    normalized = normalize_text(text)
    compact = re.sub(r"\s+", "", normalized)
    if not DIMENSION_RE.match(compact):
        return None
    compact = re.sub(r"(?:mm|MM|ｍｍ)$", "", compact)
    try:
        value = float(compact.replace(",", ""))
    except ValueError:
        return None
    if not (MIN_DIMENSION_VALUE_MM <= value <= 10_000_000.0):
        return None
    return value


def sanitize_jwcad_text(text: str) -> str:
    text = text.replace("\u3000", " ").strip()
    return text.encode("cp932", errors="replace").decode("cp932", errors="replace")


def point_to_dxf(
    point: fitz.Point,
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
) -> tuple[float, float]:
    return (
        round(x_calibrator.map(point.x), 4),
        round(y_calibrator.map(page_height) - y_calibrator.map(point.y), 4),
    )


def raw_point_to_dxf(
    x: float,
    y: float,
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
) -> tuple[float, float]:
    return (
        round(x_calibrator.map(x), 4),
        round(y_calibrator.map(page_height) - y_calibrator.map(y), 4),
    )


def bezier_to_points(
    p0: fitz.Point,
    p1: fitz.Point,
    p2: fitz.Point,
    p3: fitz.Point,
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
    divisions: int = 16,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(divisions + 1):
        t = i / divisions
        mt = 1 - t
        x = mt**3 * p0.x + 3 * mt * mt * t * p1.x + 3 * mt * t * t * p2.x + t**3 * p3.x
        y = mt**3 * p0.y + 3 * mt * mt * t * p1.y + 3 * mt * t * t * p2.y + t**3 * p3.y
        points.append(raw_point_to_dxf(x, y, page_height, x_calibrator, y_calibrator))
    return points


def extract_segments(page: fitz.Page, matrix: fitz.Matrix | None = None) -> list[Segment]:
    segments: list[Segment] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                segments.append(transform_segment(Segment(p1.x, p1.y, p2.x, p2.y), matrix))
            elif op == "re":
                rect = item[1]
                segments.extend(
                    [
                        transform_segment(Segment(rect.x0, rect.y0, rect.x1, rect.y0), matrix),
                        transform_segment(Segment(rect.x1, rect.y0, rect.x1, rect.y1), matrix),
                        transform_segment(Segment(rect.x1, rect.y1, rect.x0, rect.y1), matrix),
                        transform_segment(Segment(rect.x0, rect.y1, rect.x0, rect.y0), matrix),
                    ]
                )
            elif op == "qu":
                quad = item[1]
                pts = [quad.ul, quad.ur, quad.lr, quad.ll]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    segments.append(transform_segment(Segment(a.x, a.y, b.x, b.y), matrix))
    return segments


def extract_dimension_spans(page: fitz.Page, matrix: fitz.Matrix | None = None) -> list[TextSpan]:
    spans: list[TextSpan] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            direction = tuple(line.get("dir", (1.0, 0.0)))
            for span in line.get("spans", []):
                text = "".join(char.get("c", "") for char in span.get("chars", []))
                if not text.strip():
                    continue
                value = parse_dimension_value(text)
                if value is None:
                    continue
                spans.append(
                    transform_text_span(
                        TextSpan(
                        text=normalize_text(text),
                        value=value,
                        bbox=tuple(float(v) for v in span["bbox"]),
                        origin=tuple(float(v) for v in span["origin"]),
                        size=float(span["size"]),
                        direction=(float(direction[0]), float(direction[1])),
                        ),
                        matrix,
                    )
                )
    return spans


def extract_label_spans(page: fitz.Page, matrix: fitz.Matrix | None = None) -> list[TextSpan]:
    spans: list[TextSpan] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            direction = tuple(line.get("dir", (1.0, 0.0)))
            for span in line.get("spans", []):
                text = "".join(char.get("c", "") for char in span.get("chars", []))
                text = sanitize_jwcad_text(text)
                if not text:
                    continue
                if parse_dimension_value(text) is not None:
                    continue
                spans.append(
                    transform_text_span(
                        TextSpan(
                        text=text,
                        value=0.0,
                        bbox=tuple(float(v) for v in span["bbox"]),
                        origin=tuple(float(v) for v in span["origin"]),
                        size=float(span["size"]),
                        direction=(float(direction[0]), float(direction[1])),
                        ),
                        matrix,
                    )
                )
    return spans


def choose_dimension_line(span: TextSpan, segments: Iterable[Segment]) -> Segment | None:
    orientation = span.orientation
    best: tuple[float, Segment] | None = None

    for seg in segments:
        if seg.orientation != orientation:
            continue
        if seg.length <= 2.0:
            continue

        if orientation == "h":
            max_y = max(18.0, span.height * 2.5)
            max_x = max(30.0, span.width * 2.0, seg.length * 0.25)
            if abs(seg.cy - span.cy) > max_y:
                continue
            if abs(seg.cx - span.cx) > max_x:
                continue
            score = abs(seg.cy - span.cy) * 3.0 + abs(seg.cx - span.cx)
        else:
            max_x = max(18.0, span.width * 2.5)
            max_y = max(30.0, span.height * 2.0, seg.length * 0.25)
            if abs(seg.cx - span.cx) > max_x:
                continue
            if abs(seg.cy - span.cy) > max_y:
                continue
            score = abs(seg.cx - span.cx) * 3.0 + abs(seg.cy - span.cy)

        # 寸法線は文字の中央付近を通ることが多い。長すぎる外形線を弱く減点する。
        estimated_ratio = span.value / seg.length
        if not (MIN_SCALE_MM_PER_PT <= estimated_ratio <= MAX_SCALE_MM_PER_PT):
            continue
        score += max(0.0, math.log10(max(seg.length, 1.0)) - 2.0) * 4.0

        if best is None or score < best[0]:
            best = (score, seg)

    return best[1] if best else None


def estimate_scale(
    page: fitz.Page,
    matrix: fitz.Matrix | None = None,
) -> tuple[float | None, tuple[ScaleMatch, ...]]:
    segments = extract_segments(page, matrix)
    spans = extract_dimension_spans(page, matrix)
    matches: list[ScaleMatch] = []

    for span in spans:
        seg = choose_dimension_line(span, segments)
        if seg is None:
            continue
        ratio = span.value / seg.length
        matches.append(
            ScaleMatch(
                text=span.text,
                value=span.value,
                line_length_pt=seg.length,
                scale_mm_per_pt=ratio,
                orientation=span.orientation,
                axis_start_pt=min(seg.x1, seg.x2) if span.orientation == "h" else min(seg.y1, seg.y2),
                axis_end_pt=max(seg.x1, seg.x2) if span.orientation == "h" else max(seg.y1, seg.y2),
                line_x1_pt=seg.x1,
                line_y1_pt=seg.y1,
                line_x2_pt=seg.x2,
                line_y2_pt=seg.y2,
                text_size_pt=span.size,
            )
        )

    if not matches:
        return None, tuple()

    ratios = [m.scale_mm_per_pt for m in matches]
    median = statistics.median(ratios)
    close = [r for r in ratios if abs(r - median) / median <= 0.08]
    if close:
        median = statistics.median(close)
    return median, tuple(m for m in matches if abs(m.scale_mm_per_pt - median) / median <= 0.08)


def robust_median(values: list[float]) -> float | None:
    if not values:
        return None
    median = statistics.median(values)
    close = [v for v in values if abs(v - median) / median <= 0.08]
    return statistics.median(close or values)


def estimate_axis_scales(
    page: fitz.Page,
    matrix: fitz.Matrix | None = None,
) -> tuple[float | None, float | None, tuple[ScaleMatch, ...]]:
    segments = extract_segments(page, matrix)
    spans = extract_dimension_spans(page, matrix)
    matches: list[ScaleMatch] = []

    for span in spans:
        seg = choose_dimension_line(span, segments)
        if seg is None:
            continue
        ratio = span.value / seg.length
        matches.append(
            ScaleMatch(
                text=span.text,
                value=span.value,
                line_length_pt=seg.length,
                scale_mm_per_pt=ratio,
                orientation=span.orientation,
                axis_start_pt=min(seg.x1, seg.x2) if span.orientation == "h" else min(seg.y1, seg.y2),
                axis_end_pt=max(seg.x1, seg.x2) if span.orientation == "h" else max(seg.y1, seg.y2),
                line_x1_pt=seg.x1,
                line_y1_pt=seg.y1,
                line_x2_pt=seg.x2,
                line_y2_pt=seg.y2,
                text_size_pt=span.size,
            )
        )

    if not matches:
        return None, None, tuple()

    scale_x = robust_median([m.scale_mm_per_pt for m in matches if m.orientation == "h"])
    scale_y = robust_median([m.scale_mm_per_pt for m in matches if m.orientation == "v"])
    fallback = robust_median([m.scale_mm_per_pt for m in matches])
    scale_x = scale_x or scale_y or fallback
    scale_y = scale_y or scale_x or fallback

    if scale_x is None or scale_y is None:
        return None, None, tuple()

    filtered = tuple(
        m
        for m in matches
        if abs(m.scale_mm_per_pt - (scale_x if m.orientation == "h" else scale_y))
        / (scale_x if m.orientation == "h" else scale_y)
        <= 0.08
    )
    return scale_x, scale_y, filtered


def classify_layer(drawing: dict) -> str:
    color = drawing.get("color") or (0, 0, 0)
    width = float(drawing.get("width") or 0)
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        r, g, b = color[:3]
        if abs(r - g) < 0.03 and abs(g - b) < 0.03 and r > 0.45:
            return "PDF-GRAY"
    if width >= 0.35:
        return "PDF-WALL"
    return "PDF-LINE"


def add_layers(doc: ezdxf.document.Drawing) -> None:
    for name, color in [
        ("PDF-WALL", 7),
        ("PDF-LINE", 4),
        ("PDF-GRAY", 8),
        ("PDF-CURVE", 5),
        ("PDF-DIM", 1),
        ("PDF-TEXT", 3),
    ]:
        if name not in doc.layers:
            doc.layers.add(name, color=color)


def add_dimension_texts(
    msp: ezdxf.layouts.Modelspace,
    spans: Iterable[TextSpan],
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
    scale_x: float,
    scale_y: float,
) -> int:
    count = 0
    for span in spans:
        insert = raw_point_to_dxf(span.origin[0], span.origin[1], page_height, x_calibrator, y_calibrator)
        text_scale = (scale_x + scale_y) / 2
        height = max(1.0, span.size * text_scale)
        direction = (span.direction[0] * scale_x, -span.direction[1] * scale_y)
        angle = math.degrees(math.atan2(direction[1], direction[0]))
        text = msp.add_text(
            span.text,
            dxfattribs={
                "layer": "PDF-DIM",
                "style": "Standard",
                "height": round(height, 4),
                "rotation": round(angle, 6),
            },
        )
        text.dxf.insert = insert
        count += 1
    return count


def estimate_text_width(text: str, height: float) -> float:
    width = 0.0
    for char in text:
        width += height * (0.28 if char in ",." else 0.58)
    return width


def add_dimension_labels(
    msp: ezdxf.layouts.Modelspace,
    matches: Iterable[ScaleMatch],
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
    scale_x: float,
    scale_y: float,
) -> int:
    count = 0
    text_scale = (scale_x + scale_y) / 2
    for match in matches:
        height = max(1.0, match.text_size_pt * text_scale)
        width = estimate_text_width(match.text, height)
        mid_x = (match.line_x1_pt + match.line_x2_pt) / 2
        mid_y = (match.line_y1_pt + match.line_y2_pt) / 2
        base_x, base_y = raw_point_to_dxf(mid_x, mid_y, page_height, x_calibrator, y_calibrator)

        if match.orientation == "v":
            insert = (base_x - height * 0.65, base_y - width / 2)
            rotation = 90.0
        else:
            insert = (base_x - width / 2, base_y + height * 0.35)
            rotation = 0.0

        text = msp.add_text(
            match.text,
            dxfattribs={
                "layer": "PDF-DIM",
                "style": "Standard",
                "height": round(height, 4),
                "rotation": rotation,
            },
        )
        text.dxf.insert = (round(insert[0], 4), round(insert[1], 4))
        count += 1
    return count


def add_general_text_labels(
    msp: ezdxf.layouts.Modelspace,
    spans: Iterable[TextSpan],
    page_height: float,
    x_calibrator: AxisCalibrator,
    y_calibrator: AxisCalibrator,
    scale_x: float,
    scale_y: float,
) -> int:
    count = 0
    text_scale = (scale_x + scale_y) / 2
    for span in spans:
        height = max(1.0, span.size * text_scale)
        direction = (span.direction[0] * scale_x, -span.direction[1] * scale_y)
        angle = math.degrees(math.atan2(direction[1], direction[0]))

        if span.orientation == "v" and len(span.text) > 1:
            cell = span.height / max(1, len(span.text))
            for index, char in enumerate(span.text):
                if char.isspace():
                    continue
                px = span.x0
                py = span.y0 + cell * (index + 0.82)
                insert = raw_point_to_dxf(px, py, page_height, x_calibrator, y_calibrator)
                text = msp.add_text(
                    char,
                    dxfattribs={
                        "layer": "PDF-TEXT",
                        "style": "Standard",
                        "height": round(height, 4),
                        "rotation": 0.0,
                    },
                )
                text.dxf.insert = insert
                count += 1
            continue

        insert = raw_point_to_dxf(span.origin[0], span.origin[1], page_height, x_calibrator, y_calibrator)
        text = msp.add_text(
            span.text,
            dxfattribs={
                "layer": "PDF-TEXT",
                "style": "Standard",
                "height": round(height, 4),
                "rotation": round(angle, 6),
            },
        )
        text.dxf.insert = insert
        count += 1
    return count


def _ocr_label_spans(
    page: fitz.Page,
    dpi: int,
    log: Callable[[str], None] | None,
) -> list[TextSpan]:
    """OCRで日本語ラベルを読み取り TextSpan のリストで返す。

    重いOCR依存(rapidocr/easyocr/torch)は遅延importする。未導入・失敗時は空リストを返し、
    呼び出し側はベクター抽出のラベルへフォールバックする。座標はページ表示空間(pt)なので、
    回転行列の再適用は不要（回転0のページを前提）。
    """
    try:
        from ocr_labels import ocr_label_spans
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"OCRライブラリが見つからないためOCR補完をスキップします（{exc}）。")
        return []
    try:
        raw = ocr_label_spans(page, dpi=dpi, log=log)
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"OCR補完に失敗しました（{exc}）。図形・寸法のみで続行します。")
        return []

    spans: list[TextSpan] = []
    for (x0, y0, x1, y1, text) in raw:
        clean = sanitize_jwcad_text(text)
        if not clean:
            continue
        # 数字（寸法値）はベクター側で正確に取得できるためOCR側では捨てる。
        if parse_dimension_value(clean) is not None:
            continue
        height = max(0.0, y1 - y0)
        spans.append(
            TextSpan(
                text=clean,
                value=0.0,
                bbox=(x0, y0, x1, y1),
                origin=(x0, y1),  # ベースライン近似（左下）
                size=max(1.0, height * 0.72),
                direction=(1.0, 0.0),
            )
        )
    return spans


def calculate_extents(msp: ezdxf.layouts.Modelspace) -> tuple[float, float, float, float] | None:
    try:
        from ezdxf import bbox

        ext = bbox.extents(msp)
        if not ext.has_data:
            return None
        return (
            float(ext.extmin.x),
            float(ext.extmin.y),
            float(ext.extmax.x),
            float(ext.extmax.y),
        )
    except Exception:
        return None


def convert_pdf_to_dxf(
    input_pdf: str | Path,
    output_dxf: str | Path | None = None,
    *,
    page_number: int = 1,
    manual_scale: float | None = None,
    manual_scale_x: float | None = None,
    manual_scale_y: float | None = None,
    ocr_fallback: bool = False,
    ocr_dpi: int = 400,
    log: Callable[[str], None] | None = None,
) -> ConversionResult:
    input_path = Path(input_pdf).expanduser().resolve()
    if output_dxf is None:
        output_path = input_path.with_suffix(".dxf")
    else:
        output_path = Path(output_dxf).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"PDFが見つかりません: {input_path}")
    if input_path.suffix.lower() != ".pdf":
        raise ValueError("PDFファイルを指定してください。")

    def write_log(message: str) -> None:
        if log:
            log(message)

    write_log(f"PDFを読み込み中: {input_path}")
    pdf = fitz.open(str(input_path))
    try:
        if page_number < 1 or page_number > pdf.page_count:
            raise ValueError(f"ページ番号は 1 から {pdf.page_count} の範囲で指定してください。")
        page = pdf[page_number - 1]
        page_height = float(page.rect.height)
        coord_matrix = page.rotation_matrix
        if page.rotation:
            write_log(f"PDF回転補正: {page.rotation}度")

        if manual_scale_x and manual_scale_x > 0:
            scale_x = float(manual_scale_x)
            scale_y = float(manual_scale_y) if manual_scale_y and manual_scale_y > 0 else scale_x
            matches: tuple[ScaleMatch, ...] = tuple()
            scale_source = f"手動指定 X={scale_x:.6g} / Y={scale_y:.6g} mm/pt"
        elif manual_scale and manual_scale > 0:
            scale_x = float(manual_scale)
            scale_y = float(manual_scale)
            matches: tuple[ScaleMatch, ...] = tuple()
            scale_source = f"手動指定 X/Y={scale_x:.6g} mm/pt"
        else:
            scale_x, scale_y, matches = estimate_axis_scales(page, coord_matrix)
            if scale_x is None or scale_y is None:
                scale_x = 1.0
                scale_y = 1.0
                scale_source = "自動推定不可。1.0 mm/ptで出力"
                write_log("警告: 寸法値と寸法線を照合できませんでした。必要なら手動縮尺を指定してください。")
            else:
                h_count = sum(1 for m in matches if m.orientation == "h")
                v_count = sum(1 for m in matches if m.orientation == "v")
                scale_source = f"横寸法 {h_count}件・縦寸法 {v_count}件から自動推定"

        scale = (scale_x + scale_y) / 2
        x_calibrator = AxisCalibrator.from_intervals(
            (
                (m.axis_start_pt, m.axis_end_pt, m.value)
                for m in matches
                if m.orientation == "h"
            ),
            scale_x,
        )
        y_calibrator = AxisCalibrator.from_intervals(
            (
                (m.axis_start_pt, m.axis_end_pt, m.value)
                for m in matches
                if m.orientation == "v"
            ),
            scale_y,
        )
        write_log(f"縮尺: X={scale_x:.8g} mm/pt / Y={scale_y:.8g} mm/pt ({scale_source})")

        dxf = ezdxf.new("R12", setup=False)
        dxf.encoding = "cp932"
        dxf.header["$INSUNITS"] = 4
        add_layers(dxf)
        msp = dxf.modelspace()

        line_count = 0
        curve_count = 0
        rect_count = 0

        for drawing in page.get_drawings():
            layer = classify_layer(drawing)
            for item in drawing.get("items", []):
                op = item[0]
                if op == "l":
                    msp.add_line(
                        point_to_dxf(
                            transform_point(item[1], coord_matrix),
                            page_height,
                            x_calibrator,
                            y_calibrator,
                        ),
                        point_to_dxf(
                            transform_point(item[2], coord_matrix),
                            page_height,
                            x_calibrator,
                            y_calibrator,
                        ),
                        dxfattribs={"layer": layer},
                    )
                    line_count += 1
                elif op == "c":
                    p0 = transform_point(item[1], coord_matrix)
                    p1 = transform_point(item[2], coord_matrix)
                    p2 = transform_point(item[3], coord_matrix)
                    p3 = transform_point(item[4], coord_matrix)
                    points = bezier_to_points(
                        p0, p1, p2, p3, page_height, x_calibrator, y_calibrator
                    )
                    msp.add_polyline2d(points, dxfattribs={"layer": "PDF-CURVE"})
                    curve_count += 1
                elif op == "re":
                    rect = item[1]
                    pts = [
                        transform_point(fitz.Point(rect.x0, rect.y0), coord_matrix),
                        transform_point(fitz.Point(rect.x1, rect.y0), coord_matrix),
                        transform_point(fitz.Point(rect.x1, rect.y1), coord_matrix),
                        transform_point(fitz.Point(rect.x0, rect.y1), coord_matrix),
                    ]
                    points = [
                        point_to_dxf(pts[0], page_height, x_calibrator, y_calibrator),
                        point_to_dxf(pts[1], page_height, x_calibrator, y_calibrator),
                        point_to_dxf(pts[2], page_height, x_calibrator, y_calibrator),
                        point_to_dxf(pts[3], page_height, x_calibrator, y_calibrator),
                    ]
                    msp.add_polyline2d(points, close=True, dxfattribs={"layer": layer})
                    rect_count += 1
                elif op == "qu":
                    quad = item[1]
                    points = [
                        point_to_dxf(transform_point(quad.ul, coord_matrix), page_height, x_calibrator, y_calibrator),
                        point_to_dxf(transform_point(quad.ur, coord_matrix), page_height, x_calibrator, y_calibrator),
                        point_to_dxf(transform_point(quad.lr, coord_matrix), page_height, x_calibrator, y_calibrator),
                        point_to_dxf(transform_point(quad.ll, coord_matrix), page_height, x_calibrator, y_calibrator),
                    ]
                    msp.add_polyline2d(points, close=True, dxfattribs={"layer": layer})
                    rect_count += 1

        if matches:
            # 自動推定が成立した場合は、寸法線の中央に寸法値を配置する。
            dim_count = add_dimension_labels(
                msp, matches, page_height, x_calibrator, y_calibrator, scale_x, scale_y
            )
        else:
            # 手動で縮尺を指定した場合（または自動推定に失敗した場合）は寸法線との
            # 照合結果が無いため、寸法値をPDF上の元の位置にそのまま描画する。
            dim_count = add_dimension_texts(
                msp,
                extract_dimension_spans(page, coord_matrix),
                page_height,
                x_calibrator,
                y_calibrator,
                scale_x,
                scale_y,
            )
        label_source = "ベクター抽出"
        label_spans: Iterable[TextSpan]
        if ocr_fallback:
            ocr_spans = _ocr_label_spans(page, ocr_dpi, log)
            if ocr_spans:
                label_spans = ocr_spans
                label_source = "OCR補完"
            else:
                label_spans = extract_label_spans(page, coord_matrix)
                label_source = "ベクター抽出（OCR利用不可）"
        else:
            label_spans = extract_label_spans(page, coord_matrix)
        label_count = add_general_text_labels(
            msp,
            label_spans,
            page_height,
            x_calibrator,
            y_calibrator,
            scale_x,
            scale_y,
        )
        write_log(f"文字ラベル: {label_count}件（{label_source}）")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        dxf.saveas(output_path)
        extents = calculate_extents(msp)

        write_log(
            f"線分: {line_count} / 曲線: {curve_count} / 矩形: {rect_count}"
            f" / 寸法値: {dim_count} / 文字: {label_count}"
        )
        if extents:
            min_x, min_y, max_x, max_y = extents
            write_log(
                "DXF範囲: 幅 %.1fmm × 高さ %.1fmm" % (max_x - min_x, max_y - min_y)
            )
        write_log(f"保存しました: {output_path}")

        return ConversionResult(
            input_pdf=input_path,
            output_dxf=output_path,
            page_number=page_number,
            scale_mm_per_pt=scale,
            scale_x_mm_per_pt=scale_x,
            scale_y_mm_per_pt=scale_y,
            scale_source=scale_source,
            line_count=line_count,
            curve_count=curve_count,
            rect_count=rect_count,
            dimension_text_count=dim_count,
            label_text_count=label_count,
            scale_matches=matches,
            extents=extents,
        )
    finally:
        pdf.close()


class Pdf2DxfApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("PDF→DXF 変換")
        self.root.geometry("760x540")
        self.root.minsize(680, 460)

        self.pdf_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.page_var = tk.IntVar(value=1)
        self.auto_scale_var = tk.BooleanVar(value=True)
        self.scale_var = tk.StringVar(value="")
        self.ocr_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="PDFを選択してください。")

        self._build_ui()

    def _build_ui(self) -> None:
        tk = self.tk
        ttk = self.ttk

        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        pad = {"padx": 14, "pady": 8}

        header = ttk.Label(
            root,
            text="PDF図面をDXFへ変換",
            font=("", 15, "bold"),
        )
        header.grid(row=0, column=0, sticky="w", **pad)

        form = ttk.Frame(root)
        form.grid(row=1, column=0, sticky="ew", padx=14)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="PDF").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.pdf_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="開く", command=self.choose_pdf).grid(row=0, column=2, sticky="ew")

        ttk.Label(form, text="保存先DXF").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="保存先", command=self.choose_output).grid(row=1, column=2, sticky="ew")

        options = ttk.Frame(root)
        options.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 8))
        options.columnconfigure(6, weight=1)

        ttk.Label(options, text="ページ").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=999, width=6, textvariable=self.page_var).grid(
            row=0, column=1, sticky="w", padx=(6, 18)
        )
        ttk.Checkbutton(
            options,
            text="寸法値から縮尺を自動推定",
            variable=self.auto_scale_var,
            command=self.update_scale_state,
        ).grid(row=0, column=2, sticky="w", padx=(0, 18))
        ttk.Label(options, text="手動 mm/pt").grid(row=0, column=3, sticky="w")
        self.scale_entry = ttk.Entry(options, textvariable=self.scale_var, width=12)
        self.scale_entry.grid(row=0, column=4, sticky="w", padx=(6, 18))
        ttk.Label(options, text="Jw_cadでは縮尺 S=1/100 程度で表示").grid(
            row=0, column=5, sticky="w"
        )
        ttk.Checkbutton(
            options,
            text="日本語をOCRで補完（文字化け対策・低速）",
            variable=self.ocr_var,
        ).grid(row=1, column=2, columnspan=4, sticky="w", pady=(6, 0))

        self.log_text = tk.Text(root, height=14, wrap="word")
        self.log_text.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 8))
        self.log_text.configure(state="disabled")

        bottom = ttk.Frame(root)
        bottom.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.convert_button = ttk.Button(bottom, text="DXF変換・保存", command=self.convert)
        self.convert_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bottom, text="終了", command=root.destroy).grid(row=0, column=2, padx=(8, 0))

        self.update_scale_state()

    def update_scale_state(self) -> None:
        state = "disabled" if self.auto_scale_var.get() else "normal"
        self.scale_entry.configure(state=state)

    def write_log(self, message: str) -> None:
        def append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(0, append)

    def choose_pdf(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="PDFを選択",
            filetypes=[("PDFファイル", "*.pdf"), ("すべてのファイル", "*.*")],
        )
        if not path:
            return
        self.pdf_var.set(path)
        self.out_var.set(str(Path(path).with_suffix(".dxf")))
        self.status_var.set("変換準備ができました。")
        self.write_log(f"PDF選択: {path}")

    def choose_output(self) -> None:
        from tkinter import filedialog

        initial = self.out_var.get() or (
            str(Path(self.pdf_var.get()).with_suffix(".dxf")) if self.pdf_var.get() else ""
        )
        path = filedialog.asksaveasfilename(
            title="DXF保存先",
            defaultextension=".dxf",
            initialfile=Path(initial).name if initial else "output.dxf",
            initialdir=str(Path(initial).parent) if initial else str(Path.cwd()),
            filetypes=[("DXFファイル", "*.dxf"), ("すべてのファイル", "*.*")],
        )
        if path:
            self.out_var.set(path)

    def convert(self) -> None:
        from tkinter import messagebox

        pdf = self.pdf_var.get().strip()
        out = self.out_var.get().strip()
        if not pdf:
            messagebox.showwarning("PDF未選択", "変換するPDFを選択してください。")
            return
        if not out:
            out = str(Path(pdf).with_suffix(".dxf"))
            self.out_var.set(out)

        try:
            page_number = int(self.page_var.get())
        except Exception:
            messagebox.showwarning("ページ番号", "ページ番号は整数で入力してください。")
            return

        manual_scale: float | None = None
        if not self.auto_scale_var.get():
            try:
                manual_scale = float(self.scale_var.get())
            except Exception:
                messagebox.showwarning("手動縮尺", "手動縮尺は mm/pt の数値で入力してください。")
                return
            if manual_scale <= 0:
                messagebox.showwarning("手動縮尺", "手動縮尺は0より大きい値を入力してください。")
                return

        self.convert_button.configure(state="disabled")
        self.status_var.set("変換中...")
        self.write_log("-" * 56)

        def worker() -> None:
            try:
                result = convert_pdf_to_dxf(
                    pdf,
                    out,
                    page_number=page_number,
                    manual_scale=manual_scale,
                    ocr_fallback=self.ocr_var.get(),
                    log=self.write_log,
                )
            except Exception as exc:
                error_message = str(exc)
                self.write_log(traceback.format_exc())
                self.root.after(0, lambda: self.status_var.set("変換に失敗しました。"))
                self.root.after(
                    0,
                    lambda: messagebox.showerror("変換エラー", error_message),
                )
            else:
                self.root.after(0, lambda: self.status_var.set("DXF保存が完了しました。"))
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "完了",
                        f"DXFを保存しました。\n{result.output_dxf}\n\nJw_cadでは縮尺を S=1/100 程度に設定して表示してください。",
                    ),
                )
            finally:
                self.root.after(0, lambda: self.convert_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF図面を実寸mmのDXFへ変換します。")
    parser.add_argument("pdf", nargs="?", help="入力PDF。未指定ならGUIを起動します。")
    parser.add_argument("-o", "--output", help="保存先DXF。未指定ならPDFと同じ場所へ保存します。")
    parser.add_argument("--page", type=int, default=1, help="変換するページ番号。既定は1。")
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        help="手動縮尺 mm/pt。未指定なら寸法値から自動推定します。",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="日本語ラベルをOCRで補完する（CIDフォントの文字化け対策・要OCRライブラリ・低速）。",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=400,
        help="OCR時のレンダリング解像度。既定400。",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    if not args.pdf:
        Pdf2DxfApp().run()
        return

    result = convert_pdf_to_dxf(
        args.pdf,
        args.output,
        page_number=args.page,
        manual_scale=args.scale,
        ocr_fallback=args.ocr,
        ocr_dpi=args.ocr_dpi,
        log=print,
    )
    print()
    print("変換完了")
    print(f"入力PDF: {result.input_pdf}")
    print(f"出力DXF: {result.output_dxf}")
    print(
        "縮尺: X=%.8g mm/pt / Y=%.8g mm/pt (%s)"
        % (result.scale_x_mm_per_pt, result.scale_y_mm_per_pt, result.scale_source)
    )
    print(f"寸法値: {result.dimension_text_count}件")
    print(f"文字: {result.label_text_count}件")
    if result.scale_matches:
        print("縮尺推定に使った寸法:")
        for match in result.scale_matches:
            print(
                f"  {match.text}: {match.value:g}mm / {match.line_length_pt:.4g}pt"
                f" = {match.scale_mm_per_pt:.8g} mm/pt"
            )


if __name__ == "__main__":
    main()
