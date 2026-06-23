# PDF2DXF 積算 (PDF2DXF_SEKISAN)

ベクター PDF の図面（平面図・立面図など）を実寸(mm)の DXF に変換し、ブラウザ上のビューアで確認・積算できるツールです。
**Web アプリ**（Vercel）と **Windows デスクトップアプリ**（単体 EXE / インストーラー）の2形態で動作します。

---

## ダウンロード（利用者向け）

[Releases](../../releases) から以下のいずれかをダウンロードしてください。

- **`PDF2DXF_SEKISAN_Setup.zip`（推奨）** … Chrome などで `.exe` が「未確認」としてブロックされるのを避けられます。ダウンロード後に展開し、中の `PDF2DXF_SEKISAN_Setup.exe` を実行してください。
- **`PDF2DXF_SEKISAN_Setup.exe`** … 直接インストーラーを実行する場合。

インストール後、スタートメニューの「PDF2DXF 積算」から起動できます。

### 警告が表示される場合（署名なしアプリのため）

個人配布の署名なし `.exe` には警告が出ます。ファイルの不具合ではありません。

- **ダウンロード時**に Chrome が「未確認／危険なファイル」と表示 → **ZIP 版**を使うか、ダウンロード一覧で「保存」を選択。
- **実行時**に「Windows によって PC が保護されました」（SmartScreen）→ **「詳細情報」→「実行」** で進めます。

---

## 構成

| ファイル | 役割 |
|---|---|
| `pdf2dxf.py` | PDF→DXF 変換コア（`ezdxf` / `PyMuPDF`） |
| `ocr_labels.py` | 寸法文字の OCR 補完（`pdf2dxf.py` から遅延 import） |
| `web_pdf2dxf_app.py` | Flask アプリ本体（変換 API・ビューア配信） |
| `pdf2dxf_desktop.py` | デスクトップ版エントリ（pywebview で `files_dxf` を表示） |
| `files_dxf/` | ビューア UI 一式（EXE 同梱・Web 配信） |
| `api/index.py` | Vercel 用エントリ（`web_pdf2dxf_app` を呼ぶ） |
| `pdf2dxf_web.spec` | PyInstaller ビルド定義 |
| `build.bat` | EXE ビルド用バッチ |
| `installer.iss` | Inno Setup インストーラー定義 |
| `vercel.json` / `requirements.txt` | Web デプロイ設定 / 依存定義 |

> `dist/`（EXE・インストーラー）・`build/`・`.vercel/` などの成果物・ローカル設定は `.gitignore` 済みです。

---

## 開発環境のセットアップ

```bash
pip install -r requirements.txt
```

ローカル起動:

- Web 版: `PDF2DXF_Web起動.bat`（`python web_pdf2dxf_app.py`）→ http://127.0.0.1:5000
- CLI 変換: `PDF2DXF_起動.bat`（`python pdf2dxf.py`）

---

## EXE のビルド

```bat
build.bat
```

PyInstaller が `pdf2dxf_web.spec` を使って **`dist\PDF2DXF_SEKISAN.exe`**（単体実行ファイル）を生成します。

> OCR 系ライブラリ（easyocr など）は EXE には同梱しません（巨大化・不安定化を防ぐため）。EXE では OCR 補完は自動スキップされ、OCR はローカル Python 実行時のみ有効です。

---

## インストーラー（setup.exe）のビルド

[Inno Setup 6](https://jrsoftware.org/isdl.php) を導入後、先に `build.bat` で EXE を作ってから:

```bat
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

**`dist\PDF2DXF_SEKISAN_Setup.exe`** が生成されます。これを配布／GitHub Releases に添付します。

> winget で導入する場合: `winget install -e --id JRSoftware.InnoSetup`

---

## Web 版のデプロイ（Vercel）

```bash
vercel deploy --prod --yes
```

`api/index.py` → `web_pdf2dxf_app.py` の構成で配信されます。

---

## 配布（リリース）手順

1. `build.bat` で EXE を生成
2. `ISCC installer.iss` でインストーラーを生成
3. GitHub の **Releases** で新規タグを作成し、`dist\PDF2DXF_SEKISAN_Setup.exe` を添付
