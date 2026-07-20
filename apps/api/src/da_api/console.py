"""运营控制台（架构文档 9：管理控制台是第二产品——数据负责人的界面）。

独立于对话页（/）的完整运营端：租户维度、侧边栏导航、统计概览、
数据源生命周期（接入→体检→激活→冷启动）、语义层资产、确认队列、权限、审计、准确率。
"""

CONSOLE_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>data-agent · 运营控制台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0d1117; --panel:#161b22; --panel2:#1c2129; --border:#2d333b;
    --text:#e6edf3; --muted:#8b949e; --accent:#4da3ff; --ok:#3fb950;
    --warn:#d29922; --err:#f85149; --radius:8px; }
  * { box-sizing:border-box; margin:0; }
  body { font-family:-apple-system,'PingFang SC',sans-serif; background:var(--bg);
    color:var(--text); font-size:14px; }
  .layout { display:grid; grid-template-columns:216px 1fr; min-height:100vh; }

  aside { background:var(--panel); border-right:1px solid var(--border);
    padding:16px 0; position:sticky; top:0; height:100vh; }
  .brand { padding:0 20px 16px; font-weight:700; font-size:15px;
    border-bottom:1px solid var(--border); }
  .brand small { display:block; color:var(--muted); font-weight:400; font-size:11px; }
  nav a { display:flex; align-items:center; gap:10px; padding:9px 20px;
    color:var(--muted); text-decoration:none; border-left:2px solid transparent; }
  nav a:hover { color:var(--text); background:var(--panel2); }
  nav a.active { color:var(--accent); border-left-color:var(--accent);
    background:var(--panel2); }
  .backlink { margin-top:24px; padding:0 20px; }
  .backlink a { color:var(--muted); font-size:12px; }

  header.topbar { display:flex; justify-content:space-between; align-items:center;
    padding:14px 28px; border-bottom:1px solid var(--border); background:var(--panel);
    position:sticky; top:0; z-index:5; }
  .topbar h1 { font-size:16px; }
  .topbar .ctx { display:flex; gap:12px; align-items:center; color:var(--muted);
    font-size:13px; }
  .topbar select, .topbar input { background:var(--panel2);
    border:1px solid var(--border); color:var(--text); padding:5px 10px;
    border-radius:6px; }

  main { padding:24px 28px; max-width:1080px; }
  section { display:none; } section.active { display:block; }
  h2 { font-size:15px; margin-bottom:14px; color:var(--text); }
  .sub { color:var(--muted); font-size:12px; margin:-10px 0 16px; }

  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr));
    gap:12px; margin-bottom:24px; }
  .stat { background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:14px 16px; }
  .stat .v { font-size:24px; font-weight:700; margin-top:4px; }
  .stat .k { color:var(--muted); font-size:12px; }

  table { width:100%; border-collapse:collapse; background:var(--panel);
    border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }
  th, td { text-align:left; padding:9px 14px; border-bottom:1px solid var(--border);
    vertical-align:top; }
  th { color:var(--muted); font-weight:500; font-size:12px; background:var(--panel2); }
  tr:last-child td { border-bottom:none; }
  td .mono { font-family:ui-monospace,Menlo,monospace; font-size:12px;
    white-space:pre-wrap; word-break:break-all; color:var(--text); }

  .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; }
  .b-ok { background:rgba(63,185,80,.15); color:var(--ok); }
  .b-warn { background:rgba(210,153,34,.15); color:var(--warn); }
  .b-err { background:rgba(248,81,73,.15); color:var(--err); }
  .b-dim { background:var(--panel2); color:var(--muted); }

  button { background:var(--panel2); color:var(--text); border:1px solid var(--border);
    padding:6px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  button:hover { border-color:var(--accent); color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.primary:hover { opacity:.9; color:#fff; }
  button:disabled { opacity:.5; cursor:default; }

  .toolbar { display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap;
    align-items:center; }
  .toolbar input, .toolbar select { background:var(--panel2);
    border:1px solid var(--border); color:var(--text); padding:6px 10px;
    border-radius:6px; }
  .form { background:var(--panel); border:1px solid var(--border);
    border-radius:var(--radius); padding:16px; margin-bottom:16px;
    display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  .form input, .form select { background:var(--panel2);
    border:1px solid var(--border); color:var(--text); padding:7px 10px;
    border-radius:6px; }
  .hint { width:100%; color:var(--muted); font-size:12px; }
  .toast { position:fixed; bottom:24px; right:24px; background:var(--panel);
    border:1px solid var(--border); border-left:3px solid var(--accent);
    padding:12px 16px; border-radius:6px; max-width:420px; display:none;
    white-space:pre-wrap; font-size:13px; z-index:10; }
  details summary { cursor:pointer; color:var(--accent); font-size:12px; }
  .empty { color:var(--muted); padding:32px; text-align:center;
    background:var(--panel); border:1px dashed var(--border);
    border-radius:var(--radius); }
</style>
</head>
<body>
<div class="layout">
<aside>
  <div class="brand">data-agent<small>运营控制台</small></div>
  <nav id="nav">
    <a href="#overview" class="active">📊 概览</a>
    <a href="#sources">🔌 数据源</a>
    <a href="#semantic">🧠 语义层</a>
    <a href="#confirm">✅ 确认队列</a>
    <a href="#perm">🔐 权限</a>
    <a href="#audit">📜 审计</a>
    <a href="#eval">🎯 准确率</a>
  </nav>
  <div class="backlink"><a href="/">← 返回对话</a></div>
</aside>
<div>
  <header class="topbar">
    <h1 id="pageTitle">概览</h1>
    <div class="ctx">
      租户 <select id="tenant"><option value="default">default</option>
        <option value="">全部租户</option></select>
      操作员 <input id="uid" value="admin" size="8">
    </div>
  </header>
  <main>

  <section id="overview" class="active">
    <div class="cards" id="statCards"></div>
    <h2>快速开始</h2>
    <p class="sub">接入 SOP：添加数据源 → 集成测试 → 激活 → 一键冷启动 → 确认队列走查</p>
    <div class="toolbar">
      <button class="primary" onclick="go('sources')">去接入数据源</button>
      <button onclick="go('confirm')">处理确认队列</button>
    </div>
  </section>

  <section id="sources">
    <h2>数据集上传（零门槛接入）</h2>
    <div class="form">
      <input type="file" id="dsFile" accept=".csv,.tsv,.xlsx">
      <button class="primary" onclick="uploadDataset()">上传即问</button>
      <span class="hint">CSV/TSV/Excel → 自动建表、类型推断、首次上传自动激活</span>
    </div>
    <h2>添加数据源</h2>
    <div class="form" id="srcForm">
      <select id="srcKind" onchange="renderSrcFields()">
        <option value="clickhouse">ClickHouse</option>
        <option value="hive">Hive</option>
        <option value="sqlite">SQLite</option>
      </select>
      <input id="srcId" placeholder="source_id" size="10">
      <span id="srcFields"></span>
      <button class="primary" onclick="addSource()">连接测试并添加</button>
    </div>
    <h2>数据源列表</h2>
    <table><thead><tr><th>ID</th><th>类型</th><th>状态</th>
      <th style="width:340px">操作</th></tr></thead>
    <tbody id="srcRows"></tbody></table>
  </section>

  <section id="semantic">
    <h2>指标口径</h2>
    <table><thead><tr><th>指标</th><th>口径定义</th><th>表达式</th><th>状态</th></tr></thead>
    <tbody id="metricRows"></tbody></table>
    <h2 style="margin-top:22px">业务实体</h2>
    <table><thead><tr><th>实体</th><th>别名</th><th>物理绑定</th><th>关联路径</th></tr></thead>
    <tbody id="entityRows"></tbody></table>
  </section>

  <section id="confirm">
    <h2>待确认项 <span class="badge b-warn" id="confirmCount"></span></h2>
    <p class="sub">按使用热度排序——最常用的先确认，30 分钟覆盖 80% 日常提问</p>
    <div id="confirmList"></div>
  </section>

  <section id="perm">
    <h2>用户数据权限</h2>
    <p class="sub">渐进授权：默认无权限，按需放开库级白名单；扩权是显式操作</p>
    <div class="form">
      <input id="permUser" placeholder="用户 ID" size="12">
      <input id="permDbs" placeholder="库白名单，如 main,sales" size="20">
      <button class="primary" onclick="grantPerm()">授权</button>
    </div>
    <table><thead><tr><th>用户</th><th>租户</th><th>库白名单</th><th>状态</th></tr></thead>
    <tbody id="permRows"></tbody></table>
  </section>

  <section id="audit">
    <h2>全链路审计</h2>
    <div class="toolbar">
      阶段 <select id="auditStage" onchange="loadAudit()">
        <option value="">全部</option><option>question</option><option>generation</option>
        <option>guard</option><option>execution</option><option>presentation</option>
      </select>
      会话 <input id="auditSession" placeholder="session_id" size="12"
        onchange="loadAudit()">
      <button onclick="loadAudit()">刷新</button>
    </div>
    <table><thead><tr><th style="width:150px">时间</th><th style="width:90px">阶段</th>
      <th style="width:90px">用户</th><th>内容</th></tr></thead>
    <tbody id="auditRows"></tbody></table>
  </section>

  <section id="eval">
    <h2>准确率仪表盘</h2>
    <pre class="mono" id="evalMd" style="background:var(--panel);padding:16px;
      border-radius:8px;border:1px solid var(--border)">加载中…</pre>
  </section>

  </main>
</div>
</div>
<div class="toast" id="toast"></div>
<script>
const $ = id => document.getElementById(id);
const H = () => ({'Content-Type':'application/json','X-User-Id':$('uid').value,
                  'X-Tenant-Id':$('tenant').value||'default'});
const tenantQ = () => 'tenant='+encodeURIComponent($('tenant').value);
function toast(msg, ok=true){ const t=$('toast');
  t.style.borderLeftColor = ok?'var(--ok)':'var(--err)';
  t.textContent=msg; t.style.display='block';
  clearTimeout(t._h); t._h=setTimeout(()=>t.style.display='none', 5000); }

const TITLES={overview:'概览',sources:'数据源',semantic:'语义层',confirm:'确认队列',
  perm:'权限',audit:'审计',eval:'准确率'};
const LOADERS={overview:loadOverview,sources:loadSources,semantic:loadSemantic,
  confirm:loadConfirm,perm:loadPerm,audit:loadAudit,eval:loadEval};
function go(id){
  document.querySelectorAll('nav a').forEach(a=>
    a.classList.toggle('active', a.hash==='#'+id));
  document.querySelectorAll('main section').forEach(s=>
    s.classList.toggle('active', s.id===id));
  $('pageTitle').textContent=TITLES[id]; (LOADERS[id]||(()=>{}))();
}
document.querySelectorAll('nav a').forEach(a=>a.onclick=e=>{
  e.preventDefault(); go(a.hash.slice(1)); });
$('tenant').onchange=()=>go(document.querySelector('main section.active').id);

async function loadOverview(){
  const o=await(await fetch('/admin/overview?'+tenantQ(),{headers:H()})).json();
  const acc=o.accuracy==null?'—':Math.round(o.accuracy*100)+'%';
  $('statCards').innerHTML=[
    ['数据源',o.sources+(o.active_source?'（使用中：'+o.active_source+'）':'')],
    ['业务实体',o.entities],['指标口径',o.metrics],
    ['已验证答案',o.verified_answers],['待确认项',o.pending_confirmations],
    ['审计事件',o.audit_events],['会话数',o.sessions],['活跃用户',o.users],
    ['准确率',acc],
  ].map(([k,v])=>'<div class="stat"><div class="k">'+k+
    '</div><div class="v">'+v+'</div></div>').join('');
}

const SRC_FIELDS={
  clickhouse:[['host','host'],['port','8123'],['username','default'],['password','']],
  hive:[['host','host'],['port','10000'],['username','hive'],['database','default']],
  sqlite:[['path','/path/to.db']]};
function renderSrcFields(){
  $('srcFields').innerHTML=SRC_FIELDS[$('srcKind').value].map(([k,ph])=>
    '<input data-cfg="'+k+'" placeholder="'+k+'（如 '+ph+'）" size="14" '+
    (k==='password'?'type="password"':'')+' style="margin-right:6px">').join('');
}
async function addSource(){
  const config={};
  document.querySelectorAll('#srcFields input').forEach(i=>{
    if(i.value) config[i.dataset.cfg]=(i.dataset.cfg==='port')?
      parseInt(i.value):i.value; });
  const r=await fetch('/admin/sources',{method:'POST',headers:H(),
    body:JSON.stringify({source_id:$('srcId').value,kind:$('srcKind').value,config})});
  const d=await r.json();
  toast(r.ok?('✓ 连接成功：'+d.test.tables+' 张表\\n'+
    (d.test.table_names||[]).join(', ')):'✗ '+(d.detail||r.status), r.ok);
  loadSources();
}
async function loadSources(){
  renderSrcFields();
  const list=await(await fetch('/admin/sources',{headers:H()})).json();
  $('srcRows').innerHTML=list.map(s=>{
    const status=s.active?'<span class="badge b-ok">使用中</span>'
      :'<span class="badge b-dim">已接入</span>';
    return '<tr><td><b>'+s.source_id+'</b></td><td>'+s.kind+'</td><td>'+status+
      '</td><td>'+
      '<button onclick="testSource(\\''+s.source_id+'\\')">集成测试</button> '+
      (s.active?'':'<button onclick="activateSource(\\''+s.source_id+
        '\\')">激活</button> ')+
      '<button onclick="bootstrapSource(this,\\''+s.source_id+
        '\\')">冷启动语义层</button></td></tr>';
  }).join('')||'<tr><td colspan="4" class="empty">尚未接入数据源</td></tr>';
}
async function testSource(id){
  const r=await(await fetch('/admin/sources/'+id+'/test',
    {method:'POST',headers:H()})).json();
  toast((r.passed?'✓ 体检通过\\n':'✗ 体检未通过\\n')+
    r.checks.map(c=>(c.ok?'✓ ':'✗ ')+c.name+'：'+c.detail).join('\\n'), r.passed);
}
async function activateSource(id){
  await fetch('/admin/sources/'+id+'/activate',{method:'POST',headers:H()});
  toast('✓ 已切换到 '+id); loadSources(); }
async function bootstrapSource(btn,id){
  btn.disabled=true; btn.textContent='冷启动中…';
  const r=await(await fetch('/admin/sources/'+id+'/bootstrap',
    {method:'POST',headers:H()})).json();
  btn.disabled=false; btn.textContent='冷启动语义层';
  toast('✓ 冷启动完成\\n实体草稿 '+r.entities_created.length+
    ' 个 · 指标草稿 '+r.metrics_drafted.length+' 个\\n确认队列 +'+
    r.confirmations_queued+' · profiling '+r.profiled_columns+' 列');
}
async function uploadDataset(){
  const f=$('dsFile').files[0]; if(!f){ toast('请先选择文件', false); return; }
  const fd=new FormData(); fd.append('file',f);
  const r=await fetch('/admin/datasets/upload',
    {method:'POST',headers:{'X-User-Id':$('uid').value},body:fd});
  const d=await r.json();
  toast(r.ok?('✓ 已导入 '+d.table+'（'+d.rows+' 行）'+
    (d.activated?'，已激活，可去对话页提问':'')):'✗ '+(d.detail||r.status), r.ok);
  loadSources();
}

async function loadSemantic(){
  const d=await(await fetch('/admin/semantic/export',{headers:H()})).json();
  const L=d.semantic_layer;
  $('metricRows').innerHTML=L.metrics.map(m=>
    '<tr><td><b>'+m.name+'</b></td><td>'+(m.description||'—')+
    '</td><td class="mono">'+(m.expr||'')+'</td><td>'+
    (m.meta.verified?'<span class="badge b-ok">✓ 已确认</span>'
      :'<span class="badge b-warn">草稿</span>')+
    (m.meta.restricted?' <span class="badge b-err">受限</span>':'')+
    '</td></tr>').join('')||'<tr><td colspan="4" class="empty">暂无指标</td></tr>';
  $('entityRows').innerHTML=L.entities.map(e=>
    '<tr><td><b>'+e.name+'</b></td><td>'+(e.aliases.join(' / ')||'—')+
    '</td><td class="mono">'+e.bindings.map(b=>b.table+'.'+b.column).join('\\n')+
    '</td><td class="mono">'+e.joins.map(j=>j.sql_on).join('\\n')+
    '</td></tr>').join('')||'<tr><td colspan="4" class="empty">暂无实体</td></tr>';
}

async function loadConfirm(){
  const items=await(await fetch('/admin/confirmations',{headers:H()})).json();
  $('confirmCount').textContent=items.length||'';
  $('confirmList').innerHTML=items.map(it=>
    '<div class="form" style="display:block"><p style="margin-bottom:10px">'+
    it.question+'</p>'+it.options.map(o=>
      '<button style="margin-right:8px" onclick="answerConfirm(\\''+it.item_id+
      '\\',this.textContent)">'+o+'</button>').join('')+
    ' <span class="hint" style="width:auto">热度 '+it.priority+'</span></div>'
  ).join('')||'<div class="empty">确认队列已清空 🎉</div>';
}
async function answerConfirm(id,choice){
  await fetch('/admin/confirmations/'+id+'/answer',
    {method:'POST',headers:H(),body:JSON.stringify({choice})});
  loadConfirm();
}

async function grantPerm(){
  await fetch('/admin/users/'+$('permUser').value+'/permissions',
    {method:'PUT',headers:H(),
     body:JSON.stringify({allowed_databases:$('permDbs').value})});
  toast('✓ 已授权'); loadPerm();
}
async function loadPerm(){
  const users=await(await fetch('/admin/users',{headers:H()})).json();
  $('permRows').innerHTML=users.map(u=>
    '<tr><td>'+u.user_id+'</td><td>'+(u.tenant||'—')+'</td><td class="mono">'+
    (u.allowed_databases||'—')+'</td><td>'+
    (u.allowed_databases?'<span class="badge b-ok">已授权</span>'
      :'<span class="badge b-err">无权限</span>')+'</td></tr>'
  ).join('')||'<tr><td colspan="4" class="empty">暂无用户活动</td></tr>';
}

async function loadAudit(){
  const q='/admin/audit?limit=100&'+tenantQ()+
    '&stage='+($('auditStage').value)+'&session='+($('auditSession').value);
  const ev=await(await fetch(q,{headers:H()})).json();
  $('auditRows').innerHTML=ev.reverse().map(e=>{
    const p=e.payload||{};
    const body=p.text||p.statement||JSON.stringify(p.attribution||p.playbook||p);
    const short=body.length>160;
    const content=short?('<details><summary>'+body.slice(0,160).replace(/</g,'&lt;')+
      ' …展开</summary><div class="mono">'+body.replace(/</g,'&lt;')+
      '</div></details>'):('<span class="mono">'+body.replace(/</g,'&lt;')+'</span>');
    const extra=(p.rows!==undefined?' <span class="badge b-dim">rows='+p.rows+
      '</span>':'')+(p.reason?' <span class="badge b-err">'+p.reason+'</span>':'');
    return '<tr><td class="mono">'+e.ts.slice(0,19).replace('T',' ')+
      '</td><td><span class="badge b-dim">'+e.stage+'</span></td><td>'+
      e.identity.user_id+'</td><td>'+content+extra+'</td></tr>';
  }).join('')||'<tr><td colspan="4" class="empty">暂无审计事件</td></tr>';
}

async function loadEval(){
  const d=await(await fetch('/admin/eval-dashboard',{headers:H()})).json();
  $('evalMd').textContent=d.markdown;
}

loadOverview(); renderSrcFields();
</script>
</body>
</html>"""
