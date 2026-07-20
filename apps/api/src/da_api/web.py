"""单页 Web 前端（架构文档 9 的最小可用形态；生产升级为独立 React 应用）。

零构建：纯 HTML/JS 内嵌，FastAPI 直接托管。对话走 SSE 流式，管理台四个面板。
"""

INDEX_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>data-agent</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0f1419; --panel:#1a2027; --border:#2c3640; --text:#e6e6e6;
          --accent:#4da3ff; --muted:#8899a6; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,'PingFang SC',sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:12px 20px; border-bottom:1px solid var(--border);
           display:flex; gap:16px; align-items:center; }
  header h1 { font-size:16px; margin:0; }
  header input { background:var(--panel); border:1px solid var(--border);
                 color:var(--text); padding:4px 8px; border-radius:4px; width:100px; }
  nav button { background:none; border:none; color:var(--muted); padding:6px 10px;
               cursor:pointer; font-size:14px; }
  nav button.active { color:var(--accent); border-bottom:2px solid var(--accent); }
  main { max-width:860px; margin:0 auto; padding:20px; }
  .tab { display:none; } .tab.active { display:block; }
  #log { min-height:300px; }
  .msg { margin:10px 0; padding:10px 14px; border-radius:8px; white-space:pre-wrap; }
  .q { background:#24435f; margin-left:15%; }
  .a { background:var(--panel); margin-right:5%; border:1px solid var(--border); }
  .row { display:flex; gap:8px; margin-top:12px; }
  .row input { flex:1; background:var(--panel); border:1px solid var(--border);
               color:var(--text); padding:10px; border-radius:6px; font-size:14px; }
  .row button, .card button { background:var(--accent); color:#fff; border:none;
    padding:8px 16px; border-radius:6px; cursor:pointer; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
          padding:14px; margin:10px 0; }
  .card small { color:var(--muted); }
  pre { background:var(--panel); padding:12px; border-radius:8px; overflow-x:auto; }
</style>
</head>
<body>
<header>
  <h1>data-agent</h1>
  <label>用户 <input id="uid" value="analyst_1"></label>
  <nav>
    <button class="active" data-tab="chat">对话</button>
    <button data-tab="sources">数据源</button>
    <button data-tab="confirm">确认队列</button>
    <button data-tab="audit">审计</button>
    <button data-tab="dash">准确率</button>
  </nav>
</header>
<main>
  <section id="chat" class="tab active">
    <div id="log"></div>
    <div class="row">
      <input id="q" placeholder="问一个数据问题，例如：2026年6月的GMV是多少？"
             onkeydown="if(event.key==='Enter')ask()">
      <button onclick="ask()">发送</button>
    </div>
  </section>
  <section id="sources" class="tab">
    <div class="card">
      <p><b>上传数据集</b>（CSV/TSV/Excel，上传即问）</p>
      <input type="file" id="dsFile" accept=".csv,.tsv,.xlsx">
      <button onclick="uploadDataset()">上传</button>
      <span id="dsStatus"></span>
    </div>
    <div class="card">
      <p><b>添加数据源</b></p>
      <select id="srcKind">
        <option value="sqlite">SQLite</option>
        <option value="clickhouse">ClickHouse</option>
        <option value="hive">Hive</option>
      </select>
      <input id="srcId" placeholder="source_id">
      <input id="srcCfg" placeholder='config JSON，如 {"host":"...","port":8123}'
             style="width:320px">
      <button onclick="addSource()">连接测试并添加</button>
      <span id="srcStatus"></span>
    </div>
    <div id="srcList"></div>
  </section>
  <section id="confirm" class="tab"><div id="confirmList"></div></section>
  <section id="audit" class="tab"><pre id="auditLog">加载中…</pre></section>
  <section id="dash" class="tab"><pre id="dashMd">加载中…</pre></section>
</main>
<script>
const sid = 'web-' + Math.random().toString(36).slice(2, 10);
const H = () => ({'Content-Type':'application/json',
                  'X-User-Id':document.getElementById('uid').value,
                  'X-Tenant-Id':'default'});
const log = document.getElementById('log');

function bubble(cls, text){ const d=document.createElement('div');
  d.className='msg '+cls; d.textContent=text; log.appendChild(d);
  d.scrollIntoView(); return d; }

async function ask(){
  const input=document.getElementById('q'); const q=input.value.trim();
  if(!q) return; input.value='';
  bubble('q', q);
  const a = bubble('a', '');
  // 先建 SSE 订阅（执行者与连接解耦），再触发后台回合
  const es = new EventSource('/sessions/'+sid+'/stream?uid='+
    encodeURIComponent(document.getElementById('uid').value));
  es.onmessage = (ev)=>{ const m=JSON.parse(ev.data);
    if(m.type==='token'){ a.textContent += m.text; a.scrollIntoView(); }
    if(m.type==='done'){ a.textContent = m.text; es.close(); } };
  es.onerror = ()=> es.close();
  const r = await fetch('/sessions/'+sid+'/turns',
    {method:'POST', headers:H(), body:JSON.stringify({question:q, stream:true})});
  if(!r.ok){ a.textContent='请求失败：'+r.status; es.close(); }
}

async function loadConfirm(){
  const items = await (await fetch('/admin/confirmations',{headers:H()})).json();
  const box=document.getElementById('confirmList'); box.innerHTML='';
  if(!items.length){ box.innerHTML='<p>确认队列为空 ✅</p>'; return; }
  for(const it of items){
    const c=document.createElement('div'); c.className='card';
    c.innerHTML='<p>'+it.question+'</p><small>优先级 '+it.priority+'</small><br>';
    for(const opt of it.options){
      const b=document.createElement('button'); b.textContent=opt; b.style.margin='6px';
      b.onclick=async()=>{ await fetch('/admin/confirmations/'+it.item_id+'/answer',
        {method:'POST',headers:H(),body:JSON.stringify({choice:opt})}); loadConfirm(); };
      c.appendChild(b);
    }
    box.appendChild(c);
  }
}
async function loadAudit(){
  const ev = await (await fetch('/admin/audit?limit=30',{headers:H()})).json();
  document.getElementById('auditLog').textContent =
    ev.map(e=>e.ts+' ['+e.stage+'] '+(e.payload.text||e.payload.statement||'')
      .slice(0,120)).join('\\n') || '（暂无审计事件）';
}
async function loadDash(){
  const d = await (await fetch('/admin/eval-dashboard',{headers:H()})).json();
  document.getElementById('dashMd').textContent = d.markdown;
}

async function loadSources(){
  const list = await (await fetch('/admin/sources',{headers:H()})).json();
  const box=document.getElementById('srcList'); box.innerHTML='';
  for(const s of list){
    const c=document.createElement('div'); c.className='card';
    c.innerHTML='<b>'+s.source_id+'</b> <small>'+s.kind+'</small> '+
      (s.active?'✅ 使用中':'');
    if(!s.active){
      const b=document.createElement('button'); b.textContent='启用';
      b.style.margin='0 8px';
      b.onclick=async()=>{ await fetch('/admin/sources/'+s.source_id+'/activate',
        {method:'POST',headers:H()}); loadSources(); };
      c.appendChild(b);
    }
    const bs=document.createElement('button'); bs.textContent='冷启动语义层';
    bs.onclick=async()=>{ bs.textContent='冷启动中…';
      const r=await(await fetch('/admin/sources/'+s.source_id+'/bootstrap',
        {method:'POST',headers:H()})).json();
      bs.textContent='冷启动语义层';
      alert('实体草稿 '+r.entities_created.length+' 个，指标草稿 '+
        r.metrics_drafted.length+' 个，确认队列 +'+r.confirmations_queued); };
    c.appendChild(bs);
    box.appendChild(c);
  }
}
async function uploadDataset(){
  const f=document.getElementById('dsFile').files[0];
  if(!f){ return; }
  const fd=new FormData(); fd.append('file', f);
  const r=await fetch('/admin/datasets/upload',
    {method:'POST', headers:{'X-User-Id':document.getElementById('uid').value}, body:fd});
  const d=await r.json();
  document.getElementById('dsStatus').textContent = r.ok ?
    ('已导入表 '+d.table+'（'+d.rows+' 行）'+(d.activated?'，已启用，去对话页提问吧':'')) :
    ('失败：'+(d.detail||r.status));
  loadSources();
}
async function addSource(){
  let cfg={};
  try{ cfg=JSON.parse(document.getElementById('srcCfg').value||'{}'); }
  catch(e){ document.getElementById('srcStatus').textContent='config 不是合法 JSON'; return; }
  const r=await fetch('/admin/sources',{method:'POST',headers:H(),body:JSON.stringify({
    source_id:document.getElementById('srcId').value,
    kind:document.getElementById('srcKind').value, config:cfg})});
  const d=await r.json();
  document.getElementById('srcStatus').textContent = r.ok ?
    ('连接成功，'+d.test.tables+' 张表') : ('失败：'+(d.detail||r.status));
  loadSources();
}

document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById(b.dataset.tab).classList.add('active');
  if(b.dataset.tab==='sources') loadSources();
  if(b.dataset.tab==='confirm') loadConfirm();
  if(b.dataset.tab==='audit') loadAudit();
  if(b.dataset.tab==='dash') loadDash();
});
</script>
</body>
</html>"""
