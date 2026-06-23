# -*- coding: utf-8 -*-
"""日本語ラベルのOCR補完（PDF→DXF変換のオプション機能）。

ベクターPDFの文字がCIDフォントで化ける場合、ベクター抽出では正しい日本語を
得られない。そこでページを画像化し、ハイブリッドOCR（RapidOCRで検出、
EasyOCRで日本語を読み直す）で文字を読み取り、PDF座標系(pt)のスパンとして返す。

重要:
  - rapidocr-onnxruntime / easyocr / torch は巨大なため、ここでは**遅延import**にする。
    未インストールなら ImportError を送出し、呼び出し側が図形のみで続行できるようにする。
  - そのため Vercel(サーバーレス) や スリムなEXE では本モジュールは読み込まれず、
    OCR補完は自動的にスキップされる（変換自体は従来どおり動く）。

参考実装: ~/.claude/skills/pdf-searchable-ocr/scripts/pdf_searchable.py
"""

from __future__ import annotations

import re
from typing import Callable

# 漢字・ひらがな・カタカナ（CJK統合漢字＋かな＋互換漢字）
_CJK = re.compile(r"[々〆぀-ヿ㐀-鿿豈-﫿]")

# OCRエンジンはコストが高いので一度だけ生成して使い回す。
_ENGINES: dict = {}


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(message)


def _get_engines(log: Callable[[str], None] | None = None):
    """RapidOCR / EasyOCR を初期化（初回のみ）。未導入なら ImportError。"""
    if _ENGINES.get("init"):
        return _ENGINES["rapid"], _ENGINES["easy"]
    _log(log, "OCRエンジンを初期化しています（初回はモデル読み込みに時間がかかります）...")
    from rapidocr_onnxruntime import RapidOCR  # 遅延import（未導入なら ImportError）
    import easyocr  # 遅延import（torchを伴う・巨大）

    _ENGINES["rapid"] = RapidOCR()
    _ENGINES["easy"] = easyocr.Reader(["ja", "en"], gpu=False)
    _ENGINES["init"] = True
    _log(log, "OCRエンジンの準備が完了しました。")
    return _ENGINES["rapid"], _ENGINES["easy"]


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a[:4]
    bx0, by0, bx1, by1 = b[:4]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def _detect_tiles(arr, rapid, tile, ov, log):
    """RapidOCRでタイル分割検出 -> [(x0,y0,x1,y1,text,conf)]（画素座標）"""
    import numpy as np

    height, width = arr.shape[:2]
    raw = []
    xs = list(range(0, max(1, width - ov), tile - ov))
    ys = list(range(0, max(1, height - ov), tile - ov))
    total = len(xs) * len(ys)
    k = 0
    for ty in ys:
        for tx in xs:
            k += 1
            sub = np.ascontiguousarray(arr[ty:ty + tile, tx:tx + tile])
            try:
                res, _ = rapid(sub)
            except Exception as exc:  # noqa: BLE001
                res = None
                _log(log, f"  OCR検出 tile {k}/{total} 失敗 {exc}")
            if res:
                for box, txt, conf in res:
                    pxs = [p[0] for p in box]
                    pys = [p[1] for p in box]
                    raw.append(
                        (min(pxs) + tx, min(pys) + ty, max(pxs) + tx, max(pys) + ty, txt, float(conf))
                    )
            if k % 5 == 0 or k == total:
                _log(log, f"  OCR検出 tile {k}/{total} -> 箱={len(raw)}")
    # タイル重複由来の重複検出を除去（長文・高信頼を優先）
    raw.sort(key=lambda r: (len(r[4] or ""), r[5]), reverse=True)
    kept = []
    for r in raw:
        if not any(_iou(r, q) > 0.45 for q in kept):
            kept.append(r)
    return kept


def _reread_japanese(arr, kept, easy, log):
    """CJKを含む箱だけ EasyOCR で読み直す。小さい字は拡大、縦長は回転も試す。"""
    import numpy as np
    from PIL import Image

    height, width = arr.shape[:2]
    out = []
    jp = 0
    for i, (x0, y0, x1, y1, txt, _conf) in enumerate(kept):
        text = txt
        if _CJK.search(txt or ""):
            jp += 1
            cx0, cy0 = max(0, int(x0 - 4)), max(0, int(y0 - 4))
            cx1, cy1 = min(width, int(x1 + 4)), min(height, int(y1 + 4))
            crop = arr[cy0:cy1, cx0:cx1]
            bh, bw = crop.shape[0], crop.shape[1]
            if bh >= 8 and bw >= 4:
                if bh < 56:
                    s = 56.0 / bh
                    crop = np.array(Image.fromarray(crop).resize((max(1, int(bw * s)), 56)))
                rot = [90, 270] if (y1 - y0) > 1.6 * (x1 - x0) else None
                try:
                    r = (
                        easy.readtext(crop, detail=1, paragraph=False, rotation_info=rot)
                        if rot
                        else easy.readtext(crop, detail=1, paragraph=False)
                    )
                    jt = "".join(t for _, t, _ in r).strip()
                    if jt:
                        text = jt
                except Exception:  # noqa: BLE001
                    pass
        out.append((x0, y0, x1, y1, text))
        if (i + 1) % 100 == 0:
            _log(log, f"  日本語読み直し {i + 1}/{len(kept)}（jp {jp}）")
    return out, jp


def ocr_label_spans(
    page,
    dpi: int = 400,
    tile: int = 1600,
    overlap: int = 280,
    log: Callable[[str], None] | None = None,
) -> list[tuple[float, float, float, float, str]]:
    """ページをOCRし、ラベル文字を PDF座標系(pt) のスパンで返す。

    戻り値: [(x0, y0, x1, y1, text), ...]（左上原点・ptの矩形と読み取り文字）
    回転0のページを前提（一般的な図面）。OCRライブラリが無い場合は ImportError。
    """
    import numpy as np
    import fitz

    rapid, easy = _get_engines(log)
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    arr = np.ascontiguousarray(arr)
    _log(log, f"OCR用に描画: {pix.width}x{pix.height} @ {dpi}dpi")

    kept = _detect_tiles(arr, rapid, tile, overlap, log)
    _log(log, f"OCR検出: {len(kept)}箱")
    spans_px, jp = _reread_japanese(arr, kept, easy, log)
    _log(log, f"日本語読み直し: {jp}箱")

    scale = 72.0 / dpi  # 画素 -> pt
    out: list[tuple[float, float, float, float, str]] = []
    for (x0, y0, x1, y1, text) in spans_px:
        t = (text or "").strip()
        if not t:
            continue
        out.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale, t))
    return out
