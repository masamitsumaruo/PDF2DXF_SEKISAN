# -*- coding: utf-8 -*-
"""PDF→DXF変換とDXFビューを行うローカルWebアプリ（PDF2DXF_SEKISAN）。"""

from __future__ import annotations

import ast
import base64
import json
import math
import operator
import os
import tempfile
import traceback
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from pdf2dxf import convert_pdf_to_dxf


import sys as _sys

def _app_root() -> Path:
    if getattr(_sys, "frozen", False):
        return Path(_sys._MEIPASS)
    return Path(__file__).resolve().parent

def _is_cloud() -> bool:
    """Vercel/Render などのクラウド環境か判定する。

    クラウドでは OCR（rapidocr/easyocr/torch）や Excel 連携（pywin32）の
    依存を入れていないため、これらの機能は自動で無効化する。
    Render は環境変数 RENDER を、Vercel は VERCEL を自動で設定する。
    """
    return bool(os.environ.get("VERCEL") or os.environ.get("RENDER"))


ROOT = _app_root()
VIEWER_DIR = ROOT / "files_dxf"
WORK_DIR = Path(os.environ.get("PDF2DXF_WORK_DIR") or (Path(tempfile.gettempdir()) / "pdf2dxf_web" if _is_cloud() else ROOT / "web_work"))
JOB_DIR = WORK_DIR / "jobs"

app = Flask(__name__)
# アップロード上限。既定200MB。環境変数 MAX_UPLOAD_MB で変更できる（Render では render.yaml で指定）。
_MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))
app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_MB * 1024 * 1024


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF→DXF 積算</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#e9edec; --panel:#f7f6f3; --panel-2:#efede8; --ink:#2c3630; --muted:#8a8680;
  --line:#e0ddd7; --edge:#d8d4cc; --accent:#5a8c6e; --accent-soft:#e8efe9;
  --accent2:#d4a73b; --danger:#c43d3d; --dark:#0b0f14;
  --mono:"IBM Plex Mono","Cascadia Mono","SFMono-Regular",Consolas,monospace;
  --sans:"Noto Sans JP","Yu Gothic UI","Meiryo",system-ui,sans-serif;
  --sidebar-w:320px;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:var(--sans);color:var(--ink);background:var(--paper);overflow:hidden}
.app{display:flex;height:100%;min-height:0;overflow:hidden}
aside{width:var(--sidebar-w);flex:none;background:var(--panel);border-right:1px solid var(--edge);display:flex;flex-direction:column;min-width:0;overflow:hidden;transition:width .25s ease,opacity .2s ease;overflow-y:auto}
.app.collapsed aside{width:0;opacity:0;pointer-events:none;border-right:0}
.head{padding:18px 20px 16px;border-bottom:1px solid var(--edge);background:#2c3630}
.brand-row{display:flex;align-items:center;gap:10px}
.brand-mark{width:36px;height:36px;border-radius:8px;display:grid;place-items:center;color:#fff;background:var(--accent);box-shadow:0 1px 3px rgba(90,140,110,.3)}
.brand{font-family:var(--mono);font-size:15px;font-weight:700;letter-spacing:.06em;color:#fff}
.sub{margin-top:2px;color:rgba(255,255,255,.5);font-size:11px;line-height:1.4}
.body{padding:16px 20px 24px;overflow:auto}
.section{padding:16px 0;border-bottom:1px solid var(--line)}
.section:first-child{padding-top:0}
.label{display:flex;align-items:center;justify-content:space-between;font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.14em;margin-bottom:10px;text-transform:uppercase}
.pill{padding:2px 8px;border-radius:4px;background:var(--accent-soft);color:var(--accent);font-size:10px;letter-spacing:0;font-family:var(--mono);font-weight:600}
.drop{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;background:var(--panel-2);border:1px dashed var(--edge);border-radius:8px;padding:12px;cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover,.drop.hot{border-color:var(--accent);background:var(--accent-soft)}
.drop-icon{width:42px;height:42px;border:1px solid var(--line);border-radius:8px;display:grid;place-items:center;color:var(--accent);background:#fff}
.drop-main{font-size:13px;font-weight:700;color:var(--ink);margin-bottom:2px}
.drop-hint{font-size:11px;color:var(--muted);line-height:1.35}
input[type=file]{display:none}
.file-name{grid-column:1/-1;font-family:var(--mono);font-size:11px;color:var(--accent);word-break:break-all;min-height:14px}
.file-name:empty{display:none}
.field{margin-top:0}
.row{display:flex;gap:8px;align-items:center}
.icon-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.setting-tile{height:42px;border:1px solid var(--edge);background:var(--panel-2);color:var(--ink);border-radius:8px;cursor:pointer;display:grid;place-items:center;position:relative;transition:background .15s,border-color .15s,color .15s}
.setting-tile:hover{border-color:var(--accent);color:var(--accent);background:#f0eeea}
.setting-tile:has(input:checked){color:#fff;background:var(--accent);border-color:var(--accent)}
.setting-tile.warn{color:#8a5a00;background:#fff7e7;border-color:#eed39d}
.setting-tile.warn:has(input:checked){color:#fff;background:var(--accent2);border-color:var(--accent2)}
.setting-tile input{position:absolute;opacity:0;pointer-events:none}
.setting-tile svg,.icon-btn svg,.drop svg,.brand-mark svg,.btn svg,.menu-btn svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.hint-line{margin-top:9px;color:var(--muted);font-size:11px;line-height:1.5}
.scale-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.scale-cell{border:1px solid var(--edge);border-radius:8px;background:#fff;padding:8px 10px}
.scale-cell span{display:block;font-size:10px;color:var(--muted);margin-bottom:5px}
input[type=number],input[type=text]{width:100%;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink);padding:7px 10px;font-size:13px}
.scale-cell input{border:0;background:transparent;padding:0;font-family:var(--mono);font-weight:700}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px rgba(90,140,110,.15)}
.check{display:flex;gap:9px;align-items:center;font-size:13px;color:var(--ink);user-select:none}
.check input{width:17px;height:17px;accent-color:var(--accent)}
.btn{height:48px;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:8px;padding:0 12px;font-weight:700;cursor:pointer;width:100%;font-size:14px;display:flex;align-items:center;justify-content:center;gap:10px;transition:filter .15s}
.btn:hover{filter:brightness(1.04)}
.btn:disabled{opacity:.55;cursor:default}
.btn.secondary{background:#fff;color:var(--accent)}
.status{border:1px solid var(--edge);border-radius:8px;background:#fff;padding:10px 12px;font-size:12px;line-height:1.6;color:var(--muted);min-height:60px;white-space:pre-wrap;transition:all .2s}
.status.err{border-color:#efc5c5;background:#fff3f3;color:var(--danger)}
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:13px;height:13px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-right:7px}
.progress{position:relative;height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:8px;display:none}
.progress.on{display:block}
.progress::before{content:"";position:absolute;top:0;height:100%;width:40%;left:-40%;background:var(--accent);border-radius:3px;animation:indet 1.15s linear infinite}
@keyframes indet{0%{left:-40%}100%{left:100%}}
.meta{margin-top:10px;font-size:11px;line-height:1.7;color:var(--muted)}
.meta code{font-family:var(--mono);color:var(--ink)}
main{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--dark)}
.viewerbar{min-height:54px;background:var(--panel);border-bottom:2px solid var(--line);display:flex;align-items:center;gap:10px;padding:8px 14px;flex:none;flex-wrap:wrap}
.tool-group{display:flex;align-items:center;gap:6px;padding:3px;border:1px solid var(--edge);border-radius:8px;background:var(--panel-2)}
.tsep{width:1px;align-self:stretch;background:var(--edge);margin:4px 0}
.menu-btn{width:36px;height:34px;background:var(--panel-2);border:1px solid var(--edge);border-radius:7px;cursor:pointer;color:var(--ink);display:grid;place-items:center;transition:all .12s}
.menu-btn:hover{border-color:var(--accent);color:var(--accent);background:#f0eeea}
.viewerbar .title{font-family:var(--mono);font-size:12px;color:var(--muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.status-chip{margin-left:auto;display:flex;align-items:center;gap:7px;height:30px;padding:0 10px;border-radius:6px;border:1px solid rgba(90,140,110,.3);background:rgba(90,140,110,.1);color:var(--accent);font-size:11px;font-family:var(--mono);white-space:nowrap}
.status-dot{width:7px;height:7px;border-radius:50%;background:var(--accent)}
.viewerbar a,.viewerbar button.vb{font-size:12px;color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:6px;padding:5px 8px;background:#fff;white-space:nowrap}
iframe{border:0;width:100%;flex:1 1 auto;min-height:0;background:#0b0f14}
</style>
</head>
<body>
<div class="app" id="appRoot">
  <aside>
    <div class="head">
      <div class="brand-row">
        <div class="brand-mark"><svg viewBox="0 0 20 20" fill="none"><path d="M4 16h12" stroke="rgba(255,255,255,.9)" stroke-width="1.4" stroke-linecap="round"/><path d="M4 16L10 4l6 12" stroke="rgba(255,255,255,.7)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M7 10h6" stroke="rgba(255,255,255,.5)" stroke-width="1.2" stroke-linecap="round"/><circle cx="10" cy="4" r="1.5" fill="rgba(255,255,255,.85)"/></svg></div>
        <div>
          <div class="brand">PDF2DXF</div>
          <div class="sub">変換から拾い出しまで</div>
        </div>
      </div>
    </div>
    <div class="body">
      <section class="section">
        <div class="label">ファイル <span class="pill">PDF / DXF</span></div>
        <label class="drop" id="drop">
          <div class="drop-icon"><svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg></div>
          <div>
            <div class="drop-main">PDF/DXFを開く</div>
            <div class="drop-hint">クリック / ドラッグ＆ドロップ</div>
          </div>
          <svg viewBox="0 0 24 24"><path d="M9 18l6-6-6-6"/></svg>
          <div class="file-name" id="fileName"></div>
          <input id="file" type="file" accept=".pdf,.dxf,.PDF,.DXF">
        </label>
      </section>

      <section class="section">
        <div class="label">変換設定 <span class="pill">自動</span></div>
        <div class="icon-grid">
          <label class="setting-tile" title="寸法値から縮尺を自動推定"><input type="checkbox" id="autoScale" checked><svg viewBox="0 0 24 24"><path d="M4 19h16"/><path d="M6 19V5"/><path d="M6 5h12"/><path d="M10 8h2"/><path d="M15 8h2"/><path d="M10 12h2"/><path d="M15 12h2"/></svg></label>
          <button type="button" class="setting-tile" title="変換ページ" onclick="document.getElementById('page').focus()"><svg viewBox="0 0 24 24"><path d="M7 3h8l4 4v14H7z"/><path d="M15 3v5h5"/><path d="M10 13h6"/><path d="M10 17h4"/></svg></button>
          <label class="setting-tile warn" title="日本語をOCRで補完"><input type="checkbox" id="ocr"><svg viewBox="0 0 24 24"><path d="M4 7V5a1 1 0 0 1 1-1h2"/><path d="M17 4h2a1 1 0 0 1 1 1v2"/><path d="M20 17v2a1 1 0 0 1-1 1h-2"/><path d="M7 20H5a1 1 0 0 1-1-1v-2"/><path d="M8 12h8"/><path d="M12 8v8"/></svg></label>
          <button type="button" class="setting-tile" title="手動縮尺" onclick="autoScale.checked=false;syncScaleInputs();scaleX.focus()"><svg viewBox="0 0 24 24"><path d="M4 17l6-6 4 4 6-8"/><path d="M14 7h6v6"/></svg></button>
        </div>
        <div class="hint-line">OCRは必要な時だけ。通常は縮尺自動推定でそのまま変換。</div>
      </section>

      <section class="section">
        <div class="label">ページ</div>
        <input id="page" type="number" min="1" step="1" value="1">
      </section>

      <section class="section">
        <div class="label">縮尺 mm/pt</div>
        <div class="scale-grid">
          <label class="scale-cell"><span>X方向</span><input id="scaleX" type="text" placeholder="自動推定後に表示" disabled></label>
          <label class="scale-cell"><span>Y方向</span><input id="scaleY" type="text" placeholder="自動推定後に表示" disabled></label>
        </div>
        <div id="scaleHint" style="display:none;margin-top:6px;font-size:12px;color:#4b5563;line-height:1.5">式も使えます（例: 96.5*1000/1036）</div>
      </section>

      <section class="section">
        <button class="btn" id="convert" disabled><svg viewBox="0 0 24 24"><path d="M5 12h12"/><path d="M13 6l6 6-6 6"/><path d="M5 5v14"/></svg><span>変換して表示</span></button>
      </section>

      <section class="section">
        <div class="label">状態</div>
        <div class="status" id="status">PDFを選ぶと変換できます。DXFを選んだ場合は、そのままビューアで開きます。</div>
        <div class="progress" id="progress"></div>
        <div class="meta">
          <div>DXF形式: <code>R12 / Shift-JIS</code></div>
          <div>文字: <code>Standard</code> スタイル</div>
          <div>ビュー: パン、ホイールズーム、レイヤON/OFF対応</div>
        </div>
      </section>
    </div>
  </aside>
  <main>
    <div class="viewerbar">
      <div class="tool-group">
        <button class="menu-btn" id="menuToggle" title="サイドバー表示/非表示"><svg viewBox="0 0 24 24"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/></svg></button>
      </div>
      <div class="title" id="viewerTitle">DXFビューア</div>
      <div class="status-chip"><span class="status-dot"></span>ローカル処理</div>
    </div>
    <iframe id="viewer" name="viewer" src="/viewer/"></iframe>
  </main>
</div>
<script>
document.getElementById('menuToggle').addEventListener('click', ()=>{
  document.getElementById('appRoot').classList.toggle('collapsed');
});
const fileInput=document.getElementById('file');
const drop=document.getElementById('drop');
const fileName=document.getElementById('fileName');
const convertBtn=document.getElementById('convert');
const statusBox=document.getElementById('status');
const viewer=document.getElementById('viewer');
const viewerTitle=document.getElementById('viewerTitle');
const autoScale=document.getElementById('autoScale');
const scaleX=document.getElementById('scaleX');
const scaleY=document.getElementById('scaleY');
const scaleHint=document.getElementById('scaleHint');
let selectedFile=null;
let lastDxfB64=null, lastDxfName='';
viewer.addEventListener('load',()=>{
  if(lastDxfB64){
    viewer.contentWindow.postMessage({type:'load-dxf-bytes',b64:lastDxfB64,name:lastDxfName},'*');
    lastDxfB64=null;
  }
});

function setStatus(text, isError=false){
  statusBox.textContent=text;
  statusBox.classList.toggle('err', isError);
}
function setDownload(url, name){}
function pickFile(file){
  selectedFile=file || null;
  fileName.textContent=file ? file.name : '';
  convertBtn.disabled=!file;
  setDownload(null);
  if(file && file.name.toLowerCase().endsWith('.dxf')){
    setStatus('DXFを選択しました。「変換して表示」でビューアへ読み込みます。');
  }else if(file){
    setStatus('PDFを選択しました。「変換して表示」でDXF化します。');
  }
}
fileInput.addEventListener('change', e=>pickFile(e.target.files[0]));
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hot');}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop', e=>pickFile(e.dataTransfer.files[0]));
// 画面のどこにドロップしてもファイルを受け付ける（従来は左の小さな枠のみで、
// それ以外の場所にドロップすると何も起きなかった）
function acceptDropped(f){
  if(!f) return;
  const n=(f.name||'').toLowerCase();
  if(n.endsWith('.pdf')||n.endsWith('.dxf')) pickFile(f);
  else setStatus('PDFまたはDXFファイルをドロップしてください。', true);
}
window.addEventListener('dragover', e=>{ e.preventDefault(); drop.classList.add('hot'); });
window.addEventListener('dragleave', e=>{ if(!e.relatedTarget) drop.classList.remove('hot'); });
window.addEventListener('drop', e=>{
  e.preventDefault(); drop.classList.remove('hot');
  acceptDropped(e.dataTransfer.files[0]);
});
// ビューア(iframe)の上にドロップされたファイルはビューアから転送されてくる
window.addEventListener('message', ev=>{
  if(ev.origin!==location.origin) return;
  if(ev.data && ev.data.type==='file-dropped' && ev.data.file) acceptDropped(ev.data.file);
});
function evalScale(s){
  s=(s||'').trim();
  if(!s) return null;
  s=s.replace(/[０-９]/g, d=>String.fromCharCode(d.charCodeAt(0)-0xFEE0))
     .replace(/＊/g,'*').replace(/／/g,'/').replace(/＋/g,'+').replace(/－/g,'-')
     .replace(/．/g,'.').replace(/（/g,'(').replace(/）/g,')').replace(/　/g,' ');
  s=s.split('^').join('**');
  if(!/^[-+*/0-9eE(). ]*$/.test(s)) return NaN;
  try{ const v=Function('return ('+s+')')(); return (typeof v==='number'&&isFinite(v))?v:NaN; }catch(e){ return NaN; }
}
function updateScalePreview(){
  if(autoScale.checked){ scaleHint.style.display='none'; return; }
  scaleHint.style.display='block';
  const out=[];
  [['X',scaleX],['Y',scaleY]].forEach(p=>{
    const raw=(p[1].value||'').trim();
    if(!raw) return;
    if(!/[-+*/]/.test(raw.replace(/^[-+]/,''))) return;
    const v=evalScale(raw);
    out.push(Number.isNaN(v) ? (p[0]+': 式エラー') : (p[0]+' = '+String(Math.round(v*1e6)/1e6)+' mm/pt'));
  });
  scaleHint.textContent = out.length ? ('計算結果 → '+out.join('   /   ')) : '式も使えます（例: 96.5*1000/1036）';
}
function syncScaleInputs(){
  const ph = autoScale.checked ? '自動推定後に表示' : '例: 25 または 96.5*1000/1036';
  scaleX.disabled=autoScale.checked;
  scaleY.disabled=autoScale.checked;
  scaleX.placeholder=ph;
  scaleY.placeholder=ph;
  updateScalePreview();
}
scaleX.addEventListener('input', updateScalePreview);
scaleY.addEventListener('input', updateScalePreview);
autoScale.addEventListener('change', syncScaleInputs);
syncScaleInputs();

convertBtn.addEventListener('click', async ()=>{
  if(!selectedFile) return;
  const origLabel=convertBtn.innerHTML;
  const progress=document.getElementById('progress');
  convertBtn.disabled=true;
  convertBtn.innerHTML='<span class="spinner"></span>変換中...';
  progress.classList.add('on');
  const ocrOn=document.getElementById('ocr').checked;
  const note=ocrOn ? '（OCR補完は数分かかることがあります）' : '（PDFの図形量が多いほど時間がかかります）';
  const t0=Date.now();
  const timer=setInterval(()=>{
    setStatus('変換中です… 経過 '+Math.floor((Date.now()-t0)/1000)+' 秒'+note);
  }, 250);
  setStatus('変換中です… 経過 0 秒'+note);
  try{
    const form=new FormData();
    form.append('file', selectedFile);
    form.append('page', document.getElementById('page').value || '1');
    form.append('auto_scale', autoScale.checked ? '1' : '0');
    form.append('ocr', document.getElementById('ocr').checked ? '1' : '0');
    form.append('scale_x', scaleX.value || '');
    form.append('scale_y', scaleY.value || '');
    const res=await fetch('/api/convert', {method:'POST', body:form});
    if(res.status===413) throw new Error('PDFが大きすぎます（アップロード上限を超えています）。分割するか、ローカル版をご利用ください。');
    let data;
    try{ data=await res.json(); }
    catch(_){ throw new Error('サーバー応答エラー（'+res.status+'）。処理時間の超過またはファイルサイズ超過の可能性があります。'); }
    if(!res.ok || !data.ok) throw new Error(data.error || '変換に失敗しました。');
    if(data.scale_x) scaleX.value=data.scale_x;
    if(data.scale_y) scaleY.value=data.scale_y;
    lastDxfB64=data.dxf_b64;
    lastDxfName=data.output_name;
    viewer.src='/viewer/';
    viewerTitle.textContent=data.output_name;
    setStatus(data.message + ((data.logs&&data.logs.length) ? '\\n'+data.logs.join('\\n') : ''));
  }catch(ex){
    setStatus(ex.message, true);
  }finally{
    clearInterval(timer);
    progress.classList.remove('on');
    convertBtn.disabled=false;
    convertBtn.innerHTML=origLabel;
  }
});
</script>
</body>
</html>
"""


def ensure_dirs() -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)


def job_path(job_id: str) -> Path:
    return JOB_DIR / job_id


@app.get("/")
def index() -> Response:
    return Response(INDEX_HTML, mimetype="text/html; charset=utf-8")


@app.get("/viewer/")
def viewer_index():
    return send_from_directory(VIEWER_DIR, "index.html")


@app.get("/viewer/<path:name>")
def viewer_asset(name: str):
    return send_from_directory(VIEWER_DIR, name)


@app.get("/outputs/<job_id>/<path:name>")
def output_file(job_id: str, name: str):
    path = job_path(job_id) / name
    if not path.exists():
        return jsonify({"ok": False, "error": "ファイルが見つかりません。"}), 404
    return send_file(path, as_attachment=False)


# 縮尺入力で許可する演算（eval は使わず ast で安全に評価する）
_SCALE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_SCALE_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
# 全角→半角（数字・演算子・括弧・小数点・空白）。建築図面で全角入力されても通す。
_SCALE_NORMALIZE = str.maketrans(
    "０１２３４５６７８９＊／＋－．（）　＾",
    "0123456789*/+-.()" + " " + "^",
)


def _eval_scale_node(node):
    if isinstance(node, ast.BinOp) and type(node.op) in _SCALE_BINOPS:
        return _SCALE_BINOPS[type(node.op)](
            _eval_scale_node(node.left), _eval_scale_node(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SCALE_UNARYOPS:
        return _SCALE_UNARYOPS[type(node.op)](_eval_scale_node(node.operand))
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    raise ValueError("使用できるのは数値と + - * / ^ ( ) だけです")


def parse_scale_expression(text: str) -> float:
    """縮尺入力を数値に変換する。単なる数値のほか四則演算式も受け付ける。

    例: "25" / "18.627451*0.96" / "96.5*1000/1036"
    eval は使わず ast で許可した演算ノードのみ評価するため安全。
    """
    raw = (text or "").strip().translate(_SCALE_NORMALIZE)
    if not raw:
        raise ValueError("空です")
    raw = raw.replace("^", "**")  # ^ をべき乗として扱う
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError:
        raise ValueError("式を解釈できません")
    value = _eval_scale_node(tree.body)
    if not math.isfinite(value):
        raise ValueError("計算結果が不正です")
    return float(value)


@app.post("/api/convert")
def api_convert():
    ensure_dirs()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "ファイルを選択してください。"}), 400

    original_name = Path(uploaded.filename).name
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".pdf", ".dxf"}:
        return jsonify({"ok": False, "error": "PDFまたはDXFを指定してください。"}), 400

    job_id = uuid.uuid4().hex
    folder = job_path(job_id)
    folder.mkdir(parents=True, exist_ok=True)

    if suffix == ".dxf":
        dxf_bytes = uploaded.read()
        dxf_b64 = base64.b64encode(dxf_bytes).decode("ascii")
        message = f"DXFを読み込みました。\n元ファイル: {original_name}"
        return jsonify(
            {
                "ok": True,
                "output_name": original_name,
                "dxf_b64": dxf_b64,
                "message": message,
            }
        )

    input_path = folder / "input.pdf"
    output_path = folder / "converted.dxf"
    uploaded.save(input_path)

    logs: list[str] = []
    try:
        try:
            page = int(request.form.get("page", "1") or "1")
        except ValueError:
            return jsonify({"ok": False, "error": "ページ番号は整数で入力してください。"}), 400
        auto_scale = request.form.get("auto_scale", "1") == "1"
        ocr_fallback = request.form.get("ocr", "0") == "1"
        if ocr_fallback and _is_cloud():
            ocr_fallback = False
            logs.append("OCR補完はローカル版のみ対応のためスキップしました。")
        manual_scale_x = None
        manual_scale_y = None
        if not auto_scale:
            scale_x_text = request.form.get("scale_x", "").strip()
            scale_y_text = request.form.get("scale_y", "").strip()
            if not scale_x_text and not scale_y_text:
                return jsonify({"ok": False, "error": "手動縮尺を入力してください。"}), 400
            try:
                manual_scale_x = parse_scale_expression(scale_x_text or scale_y_text)
                manual_scale_y = parse_scale_expression(scale_y_text or scale_x_text)
            except (ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
                return jsonify(
                    {"ok": False, "error": f"縮尺を計算できません（{exc}）。例: 96.5*1000/1036"}
                ), 400
            if manual_scale_x <= 0 or manual_scale_y <= 0:
                return jsonify(
                    {"ok": False, "error": "縮尺は正の数になるよう入力してください。"}
                ), 400

        result = convert_pdf_to_dxf(
            input_path,
            output_path,
            page_number=page,
            manual_scale_x=manual_scale_x,
            manual_scale_y=manual_scale_y,
            ocr_fallback=ocr_fallback,
            log=logs.append,
        )
    except Exception as exc:
        (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return jsonify({"ok": False, "error": str(exc), "logs": logs}), 500

    output_name = Path(original_name).with_suffix(".dxf").name
    dxf_bytes = output_path.read_bytes()
    dxf_b64 = base64.b64encode(dxf_bytes).decode("ascii")
    message = "\n".join(
        [
            "変換が完了しました。",
            f"元ファイル: {original_name}",
            f"縮尺: X={result.scale_x_mm_per_pt:.8g} / Y={result.scale_y_mm_per_pt:.8g} mm/pt",
            f"寸法値: {result.dimension_text_count}件 / 文字: {result.label_text_count}件",
        ]
    )
    return jsonify(
        {
            "ok": True,
            "output_name": output_name,
            "dxf_b64": dxf_b64,
            "scale_x": f"{result.scale_x_mm_per_pt:.8g}",
            "scale_y": f"{result.scale_y_mm_per_pt:.8g}",
            "message": message,
            "logs": logs,
        }
    )


def _excel_value(raw_value: str):
    normalized = raw_value.replace(",", "")
    try:
        if normalized and normalized.replace(".", "", 1).replace("-", "", 1).isdigit():
            return float(normalized) if "." in normalized else int(normalized)
    except Exception:
        pass
    return raw_value


def _a1(row: int, col: int) -> str:
    """行・列番号からA1形式のセル番地（例: B10）を組み立てる。

    win32comの動的ディスパッチでは cell.Address(False, False) が
    'str' object is not callable で失敗するため、手動で組み立てる。
    """
    letters = ""
    n = int(col)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{int(row)}"


def _import_excel_com():
    """Excel連携に必要な pywin32 を読み込む。未導入なら分かりやすい RuntimeError。"""
    try:
        import pythoncom
        import pywintypes  # noqa: F401  ProgID→CLSID解決に使用
        import win32com.client
        import win32com.client.dynamic  # noqa: F401  frozen EXEでの取りこぼし防止＋動的接続用

        return pythoncom, win32com.client
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Excel連携に必要な pywin32 が見つかりません。"
            "ローカル版（EXEまたは python 実行）で使ってください。"
        ) from exc


# Excelブックとして扱う拡張子（ROTフォールバック用）
_EXCEL_BOOK_EXTS = (".xls", ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".csv")


def _get_excel(win32):
    """起動中のExcelに『動的ディスパッチ』で接続する（gencache非依存）。

    以前は win32.GetActiveObject("Excel.Application") を使っていたが、これは内部で
    gencache（%TEMP%\\gen_py の型キャッシュ）を経由するため、キャッシュが壊れると
    Excelを開いていても "起動中のExcelが見つかりません" になり転記できなくなる事象があった。
    ここでは gencache を一切使わない dynamic.Dispatch のみで接続し、
    さらに失敗時は ROT（実行中オブジェクトテーブル）を走査して、開いているブックから
    Application を取得するフォールバックを行う。勝手に空のExcelは起動しない（接続のみ）。
    """
    import pythoncom
    import pywintypes
    from win32com.client import dynamic

    def _as_dispatch(unknown):
        # GetActiveObject/ROT は IUnknown を返すため、IDispatch へ変換してから
        # 動的ラッパー化する（gencache は経由しない）。
        return dynamic.Dispatch(unknown.QueryInterface(pythoncom.IID_IDispatch))

    # 1) 主経路: Excel.ApplicationのCLSIDを直接掴む（gencache不使用）
    try:
        clsid = pywintypes.IID("Excel.Application")
        return _as_dispatch(pythoncom.GetActiveObject(clsid))
    except Exception:  # noqa: BLE001
        pass

    # 2) フォールバック: ROTを走査し、開いているExcelブックのmonikerからApplicationを得る
    try:
        rot = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        for moniker in rot.EnumRunning():
            try:
                name = moniker.GetDisplayName(ctx, None)
            except Exception:  # noqa: BLE001
                continue
            if not name or not name.lower().endswith(_EXCEL_BOOK_EXTS):
                continue
            try:
                book = _as_dispatch(rot.GetObject(moniker))
                app = book.Application
                if app is not None:
                    return app
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    raise RuntimeError(
        "起動中のExcelが見つかりません。Excelを開き、"
        "転記先のセルを選択してから、もう一度お試しください。"
    )


@app.route("/api/excel/ping", methods=["GET", "POST", "OPTIONS"])
def api_excel_ping():
    """Excel転記モードの事前チェック。サーバー稼働とExcel接続状況を返す。"""
    if request.method == "OPTIONS":
        return Response(status=204)
    if _is_cloud():
        return jsonify(
            {
                "ok": True,
                "server": True,
                "excel": False,
                "message": "Excel転記はローカル版でのみ利用できます。",
            }
        )
    info = {"ok": True, "server": True, "excel": False}
    try:
        pythoncom, win32 = _import_excel_com()
        pythoncom.CoInitialize()
        try:
            excel = _get_excel(win32)
            try:
                book = excel.ActiveWorkbook
                info["workbook"] = str(book.Name) if book else None
                cell = excel.ActiveCell
                if cell is not None:
                    info["cell"] = _a1(int(cell.Row), int(cell.Column))
            except Exception:  # noqa: BLE001
                pass
            info["excel"] = True
        finally:
            pythoncom.CoUninitialize()
    except RuntimeError as exc:
        info["message"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        info["message"] = f"Excelの状態を確認できませんでした: {exc}"
    return jsonify(info)


@app.route("/api/excel/reset", methods=["POST", "OPTIONS"])
def api_excel_reset():
    if request.method == "OPTIONS":
        return Response(status=204)
    if _is_cloud():
        return jsonify({"ok": False, "error": "Excel転記はローカル起動時のみ利用できます。"}), 501
    try:
        pythoncom, win32 = _import_excel_com()
        pythoncom.CoInitialize()
        try:
            excel = _get_excel(win32)
            cell = excel.ActiveCell
            if cell is None:
                raise RuntimeError("アクティブなセルがありません。Excelでセルを選択してください。")
            address = _a1(int(cell.Row), int(cell.Column))
        finally:
            pythoncom.CoUninitialize()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Excelの開始セルを取得できませんでした: {exc}"}), 200
    return jsonify({"ok": True, "cell": address})


@app.route("/api/excel/write", methods=["POST", "OPTIONS"])
def api_excel_write():
    if request.method == "OPTIONS":
        return Response(status=204)
    if _is_cloud():
        return jsonify({"ok": False, "error": "Excel転記はローカル起動時のみ利用できます。"}), 501

    data = request.get_json(silent=True) or {}
    raw_value = str(data.get("value", "")).strip()
    if not raw_value:
        return jsonify({"ok": False, "error": "転記する値が空です。"}), 400

    try:
        pythoncom, win32 = _import_excel_com()
        pythoncom.CoInitialize()
        try:
            excel = _get_excel(win32)
            # 現在の選択セル（アクティブセル）に書き込む
            cell = excel.ActiveCell
            if cell is None:
                raise RuntimeError(
                    "転記先のセルがありません。Excelでブックを開き、セルを選択してください。"
                )
            sheet = cell.Worksheet
            workbook = sheet.Parent
            row = int(cell.Row)
            col = int(cell.Column)
            value = _excel_value(raw_value)
            cell.Value = value
            address = _a1(row, col)

            # 転記後のアクティブセル移動方向。move='right'で右(列+1)、それ以外は下(行+1)。既定=下。
            move = str(data.get("move", "down")).lower()
            if move == "right":
                next_row, next_col = row, col + 1
            else:
                next_row, next_col = row + 1, col
            # 画面上の選択枠も動かす（＝Enter/Tabと同じ挙動）。Excelが前面でないと Select が
            # 一時的に失敗することがあるため数回リトライする。
            next_cell = sheet.Cells(next_row, next_col)
            for _ in range(3):
                try:
                    workbook.Activate()
                    sheet.Activate()
                    next_cell.Select()
                    break
                except Exception:  # noqa: BLE001
                    pass
            next_address = _a1(next_row, next_col)
        finally:
            pythoncom.CoUninitialize()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Excelへ転記できませんでした: {exc}"}), 200

    return jsonify({"ok": True, "value": raw_value, "cell": address, "next_cell": next_address})


if __name__ == "__main__":
    ensure_dirs()
    app.run(host="127.0.0.1", port=5055, debug=False)
