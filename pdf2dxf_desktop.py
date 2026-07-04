# -*- coding: utf-8 -*-
"""PDF→DXF 積算 デスクトップアプリ（EXE単体で動作）"""

import base64
import os
import socket
import threading
import time
import urllib.request
import webview
from web_pdf2dxf_app import app, ensure_dirs


def _find_free_port(preferred: int = 5055) -> int:
    """空いているポートを返す。

    前回のプロセスが異常終了してポート5055を握ったままだと、次回起動時に
    Flaskがバインドできず「白い画面のまま固まる」事象になるため、
    5055が使えない場合は近くの空きポートへ自動退避する。
    （ビューアのExcel連携は同一オリジン相対パスを優先するためポートが変わっても動く）
    """
    for port in (preferred, *range(preferred + 1, preferred + 26)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # 全て塞がっている場合はOSに任せて一時ポートを取る
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _find_free_port()


def start_server():
    ensure_dirs()
    # threaded=True: シェル画面・ビューア(iframe)・Excel API の同時アクセスを捌けるようにする。
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


def _wait_server_ready(timeout_sec: float = 15.0) -> None:
    """ウィンドウを開く前にサーバーの応答を待つ（起動直後の読み込み失敗・白画面を防ぐ）。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)


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
    _wait_server_ready()
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
