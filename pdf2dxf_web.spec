# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
ROOT = os.path.abspath('.')

a = Analysis(
    ['pdf2dxf_desktop.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[
        ('files_dxf', 'files_dxf'),
    ],
    hiddenimports=[
        'win32com',
        'win32com.client',
        'win32com.client.dynamic',
        'win32com.client.gencache',
        'win32gui',
        'win32con',
        'pythoncom',
        'pywintypes',
        'flask',
        'flask.json',
        'jinja2',
        'markupsafe',
        'werkzeug',
        'ezdxf',
        'fitz',
        'pymupdf',
        'webview',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'bottle',
        'proxy_tools',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OCR系(ocr_labels.py が関数内で遅延importする巨大ライブラリ)はEXEに同梱しない。
    # 同梱するとEXEが数百MB化・ビルドも不安定になる。EXEでは実行時にImportErrorとなり、
    # OCR補完は自動スキップされる(設計どおり)。OCRはローカルpython実行時のみ有効。
    # ※ numpy は ezdxf が必須とするため除外しないこと（除外するとEXEが起動しない）。
    excludes=[
        'matplotlib', 'tkinter', 'pandas', 'openpyxl', 'PIL',
        'easyocr', 'rapidocr_onnxruntime', 'onnxruntime',
        'torch', 'torchvision',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDF2DXF_SEKISAN',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=os.path.join(ROOT, 'files_dxf', 'favicon.ico'),
    version=os.path.join(ROOT, 'version_info.txt'),  # アプリ名・発行元等のバージョン情報を埋め込む
)
