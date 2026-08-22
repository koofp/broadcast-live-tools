# -*- coding: utf-8 -*-
"""bilive 运维面板 (127.0.0.1:9090)
启动: pythonw panel.py   （计划任务 bilive-panel 登录自启）
依赖: pip install fastapi uvicorn
"""
import json, subprocess, threading, time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parent
VIDEOS = ROOT / "bilive-docker" / "Videos"
PROMPT_FILE = ROOT / "prompt.txt"
LOCK = ROOT / "run.lock"
PS = str(ROOT / "status.ps1")
PROC = str(ROOT / "process_all.ps1")
DEFAULT_PROMPT = """你是资深直播内容分析师。以下字幕来自语音识别，可能含同音误听，
请结合语境自行纠正（游戏术语、DOTA2英雄名、装备名等）。

字幕：
{srt}

请输出（Markdown）：
## 一句话总结
## 核心主题（不超过20字）
## 讨论要点（按时间顺序，标注[mm:ss]，每条不超过25字）
## 金句/名场面（如有，含时间戳）
## 疑似识别错误对照表（原文→推测正确词）
"""

app = FastAPI(title="bilive panel")


@app.get("/api/status")
def api_status():
    """复用 status.ps1 的权威逻辑（含 fake-ip 指纹/增长采样），解析末尾 JSON 行"""
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    js = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                js = json.loads(line); break
            except Exception:
                continue
    d = dict(js or {})
    ctn = subprocess.run(["docker", "ps", "--filter", "name=bilive_docker", "--format", "{{.Status}}"],
                         capture_output=True, text=True).stdout.strip()
    d["container"] = ctn or "stopped"
    d["locked"] = LOCK.exists()
    return d


@app.get("/api/files")
def api_files():
    rows = []
    if not VIDEOS.exists():
        return rows
    for room in sorted(VIDEOS.iterdir()):
        if not room.is_dir():
            continue
        for m in sorted(room.glob("*.mp4")) + sorted(room.glob("*.flv")):
            base = m.with_suffix("")
            if m.suffix == ".flv" and base.with_suffix(".mp4").exists():
                continue  # 已 remux 的 flv 不重复列出
            rows.append({"room": room.name, "name": m.name,
                         "size_gb": round(m.stat().st_size/2**30, 2),
                         "mtime": time.strftime("%m-%d %H:%M", time.localtime(m.stat().st_mtime)),
                         "srt": base.with_suffix(".srt").exists(),
                         "summary": base.with_suffix(".summary.md").exists()})
    return rows


@app.get("/api/summary")
def api_summary(room: str, name: str):
    s = (VIDEOS / room / name).with_suffix(".summary.md")
    if not s.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"md": s.read_text(encoding="utf-8")}


@app.post("/api/process")
async def api_process(req: Request):
    body = await req.json()
    name = body.get("name")
    if LOCK.exists():
        return JSONResponse({"started": False, "reason": "另一处理进程运行中"}, status_code=409)

    def launch(single_path=None):
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", PROC] + (["-One", single_path] if single_path else [])
        subprocess.Popen(args, cwd=str(ROOT))

    if name and name != "__all__":
        hit = None
        for room in VIDEOS.iterdir():
            if not room.is_dir():
                continue
            cand = room / name
            if cand.exists():
                hit = str(cand); break
        if not hit:
            return JSONResponse({"started": False, "reason": "file not found"}, status_code=404)
        threading.Thread(target=launch, args=(hit,), daemon=True).start()
        return {"started": True, "scope": name}
    threading.Thread(target=launch, daemon=True).start()
    return {"started": True, "scope": "__all__"}


@app.get("/api/prompt")
def get_prompt():
    return {"prompt": PROMPT_FILE.read_text(encoding="utf-8") if PROMPT_FILE.exists() else DEFAULT_PROMPT}


@app.post("/api/prompt")
async def set_prompt(req: Request):
    b = await req.json()
    text = b.get("prompt", "")
    if not text.strip() or "{srt}" not in text:
        return JSONResponse({"saved": False,
                             "reason": "提示词不能为空且必须包含 {srt} 占位符"}, status_code=400)
    PROMPT_FILE.write_text(text, encoding="utf-8")
    return {"saved": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>bilive 控制台</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#232833;--fg:#e6e9ef;--mut:#8b93a3;--acc:#4f8cff;--ok:#3fb96f;--warn:#e8b339;--bad:#e5534b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 "Segoe UI",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:22px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.card .v{font-size:22px;font-weight:600;margin-top:2px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;border:1px solid var(--line)}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:1px 9px;border-radius:99px;font-size:11px}
.b-done{background:#12351f;color:var(--ok)}.b-srt{background:#3a3012;color:var(--warn)}.b-none{background:#38201f;color:#ff8f88}
.b-flv{background:#12283a;color:#6cb6ff}
button{background:var(--acc);border:none;color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer;font-size:12px}
button:hover{filter:brightness(1.15)}button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg)}
textarea{width:100%;min-height:180px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:12px;font:12px/1.6 Consolas,monospace;resize:vertical}
.row{display:flex;gap:10px;align-items:center;margin:8px 0 26px}
#log{background:#0b0d10;border:1px solid var(--line);border-radius:10px;padding:10px 14px;color:var(--mut);font:11px Consolas,monospace;max-height:120px;overflow:auto;white-space:pre-wrap}
h2{font-size:14px;margin:26px 0 10px;color:var(--mut);font-weight:600}
#sumview{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;display:none;max-height:420px;overflow:auto}
select{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:6px}
</style></head><body><div class="wrap">
<h1>bilive 控制台</h1><div class="sub">本地存档模式 · 录制 → 转写(faster-whisper small) → AI 总结(ox-alpha) · 127.0.0.1:9090</div>
<div class="grid">
 <div class="card"><div class="k">容器</div><div class="v" id="ctn">-</div></div>
 <div class="card"><div class="k">最新分段更新</div><div class="v" id="age">-</div></div>
 <div class="card"><div class="k">磁盘剩余</div><div class="v" id="disk">-</div></div>
 <div class="card"><div class="k">可录天数</div><div class="v" id="days">-</div></div>
 <div class="card"><div class="k">积压待处理</div><div class="v" id="backlog">-</div></div>
 <div class="card"><div class="k">DNS 劫持(Clash)</div><div class="v" id="clash">-</div></div>
</div>
<h2>分段列表（全部房间）</h2>
<table id="tbl"><thead><tr><th>房间</th><th>文件</th><th>大小</th><th>时间</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table>
<div class="row"><button onclick="procAll()">▶ 处理全部积压</button><span id="procstate"></span></div>
<h2>总结提示词（保存后对后续处理生效；必须含 {srt} 占位符）</h2>
<textarea id="prompt"></textarea>
<div class="row"><button onclick="savePrompt()">保存提示词</button><button class="ghost" onclick="loadPrompt()">重新读取</button></div>
<h2>已完成的总结</h2><div class="row"><select id="sumsel" onchange="showSum()"></select></div>
<div id="sumview"></div>
<div style="height:14px"></div><div id="log">ready.</div>
</div>
<script>
const $=id=>document.getElementById(id);
function log(s){const l=$('log');l.textContent+=s+"\\n";l.scrollTop=l.scrollHeight;}
let busy=false;
async function refreshStatus(){
 const d=await(await fetch('/api/status')).json();
 $('ctn').textContent=d.container||'-';$('ctn').className=(d.container||'').startsWith('Up')?'ok':'bad';
 const a=d.newest_age_min;$('age').textContent=a==null?'-':(a<15?a+'分钟前':('⚠ '+a+'分钟前'));
 $('age').className=(a==null||a>45)?'bad':(a>15?'warn':'ok');
 $('disk').textContent=(d.free_gb??'-')+' GB';$('days').textContent=d.days??'-';
 $('days').className=d.days<1?'bad':(d.days<2?'warn':'ok');
 $('backlog').textContent=d.backlog??'-';$('backlog').className=(d.backlog>0)?'warn':'ok';
 const fp=(d.issues||[]).includes('fakeip');
 $('clash').textContent=fp?'被劫持!!':'正常';$('clash').className=fp?'bad':'ok';
 busy=!!d.locked;$('procstate').textContent=busy?'后台处理中…(锁激活)':'';
 document.querySelectorAll('button').forEach(b=>{if(b.textContent.startsWith('▶')||b.textContent==='处理')b.disabled=busy;});
}
async function refreshFiles(){
 const rows=await(await fetch('/api/files')).json();
 const tb=$('tbl').querySelector('tbody');tb.innerHTML='';
 const sel=$('sumsel');sel.innerHTML='';
 for(const r of rows){
  let badge='<span class="badge b-none">未处理</span>';
  if(r.summary)badge='<span class="badge b-done">已总结</span>';
  else if(r.srt)badge='<span class="badge b-srt">已转写</span>';
  else if(r.name.endsWith('.flv'))badge='<span class="badge b-flv">原始flv</span>';
  const tr=document.createElement('tr');
  tr.innerHTML=`<td>${r.room}</td><td>${r.name}</td><td>${r.size_gb}GB</td><td>${r.mtime}</td><td>${badge}</td>
   <td>${r.summary?`<button class="ghost" onclick="viewSum('${r.room}','${r.name}')">看总结</button>`
   :`<button ${busy?'disabled':''} onclick="procOne('${r.room}','${r.name}')">处理</button>`}</td>`;
  tb.appendChild(tr);
  if(r.summary){const o=document.createElement('option');o.textContent=r.room+'/'+r.name;o.value=r.room+'|'+r.name;sel.appendChild(o);}
 }
}
async function procOne(room,name){if(!confirm('处理 '+name+' ?'))return;log('提交: '+name);
 const r=await fetch('/api/process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})});
 if(r.status===409){log('拒绝: 另一处理进程运行中');return;}
 log('已启动(后台执行,锁保护)');poll();}
async function procAll(){if(!confirm('处理全部积压分段?'))return;
 const r=await fetch('/api/process',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'__all__'})});
 if(r.status===409){log('拒绝: 已有进程在跑');return;}
 log('批量已启动');poll();}
async function loadPrompt(){$('prompt').value=(await(await fetch('/api/prompt')).json()).prompt;}
async function savePrompt(){const r=await(await fetch('/api/prompt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:$('prompt').value})})).json();
 log(r.saved?'提示词已保存':('保存失败: '+r.reason));if(!r.saved)alert(r.reason);}
async function viewSum(room,name){const d=await(await fetch(`/api/summary?room=${encodeURIComponent(room)}&name=${encodeURIComponent(name)}`)).json();
 const v=$('sumview');v.style.display='block';v.innerHTML=(d.md||'').replace(/\\n/g,'<br>').replace(/^## (.*)$/gm,'<b>$1</b>');}
function showSum(){const v=$('sumsel').value;if(v){const parts=v.split('|');viewSum(parts[0],parts[1]);}}
let polling=null;
function poll(){if(polling)clearInterval(polling);
 polling=setInterval(()=>{refreshStatus();refreshFiles();},8000);
 setTimeout(()=>{clearInterval(polling);refreshStatus();refreshFiles();},900000);}
refreshStatus();refreshFiles();loadPrompt();
setInterval(refreshStatus,30000);
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9090, log_level="warning")