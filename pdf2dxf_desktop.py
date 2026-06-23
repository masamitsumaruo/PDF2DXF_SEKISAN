# -*- coding: utf-8 -*-
"""PDF→DXF 積算 デスクトップアプリ（EXE単体で動作）"""

import base64
import os
import threading
import webview
from web_pdf2dxf_app import app, ensure_dirs

PORT = 5055


def start_server():
    ensure_dirs()
    # threaded=True: シェル画面・ビューア(iframe)・Excel API の同時アクセスを捌けるようにする。
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


class Api:
    """JS から呼ばれるブリッジ。図面画像(PNG)をA4 PDFにして保存ダイアログで保存する。"""

    def __init__(self):
        self.window = None

    def save_pdf(self, data_url, filename):
        try:
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            png = base64.b64decode(data_url)
            if not filename:
                filename = "drawing.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            result = self.window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=filename,
                file_types=("PDF ファイル (*.pdf)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            # PNG → A4 PDF（向き自動・余白つきでフィット）
            import fitz
            src = fitz.open(stream=png, filetype="png")
            r = src[0].rect
            landscape = r.width >= r.height
            pw, ph = (842, 595) if landscape else (595, 842)  # A4 (pt)
            margin = 18
            doc = fitz.open()
            page = doc.new_page(width=pw, height=ph)
            page.insert_image(
                fitz.Rect(margin, margin, pw - margin, ph - margin),
                stream=png, keep_proportion=True,
            )
            doc.save(path)
            doc.close()
            src.close()
            return {"ok": True, "path": path}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}


def _force_quit(*_args):
    """ウィンドウの×（閉じる）を押した瞬間に、プロセスを確実に終了させる。

    pywebview(WebView2/EdgeChromium) + Flask + Excel COM の構成では、通常の終了処理が
    .NET ファイナライザや解放されない COM 参照の待ちでハングし、
    「×を押しても閉じない／固まる（フリーズ）」状態や、プロセスが残ったまま
    ポート5055を握り続けて次回起動に失敗する事象が起きることがある。
    os._exit はクリーンアップを一切行わず即座にプロセスを落とすため、この種のハングを
    確実に回避できる。（このアプリは閉じる時に保存待ちの状態を持たないため即時終了で問題ない。）

    closing イベントは GUI スレッド上で同期実行（should_lock=True）されるため、
    ハングしうる後処理が走る前にここで終了できる。
    """
    os._exit(0)


if __name__ == "__main__":
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    api = Api()
    window = webview.create_window(
        "PDF→DXF 積算",
        f"http://127.0.0.1:{PORT}",
        min_size=(800, 500),
        maximized=True,
        js_api=api,
    )
    api.window = window
    # ×（閉じる）でハング／ゾンビ化しないよう、閉じる要求の時点で確実に終了させる。
    window.events.closing += _force_quit
    webview.start()
    # webview.start() が正常に戻った場合の保険（念のため即時終了）。
    os._exit(0)
