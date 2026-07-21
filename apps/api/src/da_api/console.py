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
      <th style="width:400px">操作</th></tr></thead>
    <tbody id="srcRows"></tbody></table>

    <div id="metaBrowser" style="display:none;margin-top:22px">
      <h2>元数据浏览 · <span id="metaSrcId"></span></h2>
      <p class="sub">勾选要集成的表 → 系统 profiling + 证据图归一 → 产出语义层草稿与确认题</p>
      <div class="toolbar">
        <button class="primary" onclick="integrateTables()">集成所选表到语义层</button>
        <button onclick="$('metaBrowser').style.display='none'">关闭</button>
      </div>
      <table><thead><tr><th style="width:36px"></th><th>表</th><th>行数</th>
        <th>列结构</th></tr></thead>
      <tbody id="metaRows"></tbody></table>
    </div>
  </section>

  <section id="semantic">
    <h2>映射矩阵 <span class="sub" style="display:inline;margin:0">
      行 = 语义对象，列 = 使用中数据源的表，格 = 绑定</span></h2>
    <div class="toolbar">
      <button onclick="newEntity()">＋ 新建实体</button>
      <button onclick="newRole()">＋ 新建属性（语义角色）</button>
      <label style="font-size:12px;color:var(--muted)">
        <input type="checkbox" id="onlyUnbound" onchange="renderMatrix()"> 仅看未绑定</label>
    </div>
    <div style="overflow-x:auto">
      <table style="min-width:640px"><thead id="matrixHead"></thead>
      <tbody id="matrixBody"></tbody></table>
    </div>

    <div id="cellPicker" style="display:none;margin-top:14px" class="form">
      <div style="width:100%"><b id="pickerTitle" style="font-size:13px"></b></div>
      <div style="width:100%;display:flex;gap:8px">
        <button id="tabCol" class="primary" onclick="pickerMode('col')">选择列</button>
        <button id="tabExpr" onclick="pickerMode('expr')">ƒ SQL 转换</button>
        <span style="flex:1"></span>
        <button onclick="$('cellPicker').style.display='none'">关闭</button>
      </div>
      <div id="pickerCols" style="width:100%;display:flex;gap:6px;flex-wrap:wrap"></div>
      <div id="pickerExpr" style="width:100%;display:none">
        <input id="exprInput" placeholder="SQL 转换片段，如 strftime('%Y-%m', pay_dt)"
          style="width:60%">
        <select id="exprSourceCol" style="max-width:150px"></select>
        <button onclick="previewExpr()">校验并预览</button>
        <button class="primary" onclick="saveExprBinding()">保存绑定</button>
        <pre id="exprPreview" class="mono" style="margin-top:8px;font-size:11px;
          color:var(--muted);white-space:pre-wrap"></pre>
      </div>
    </div>

    <h2 style="margin-top:24px">指标配置
      <button class="primary" style="margin-left:10px;font-size:12px"
        onclick="editMetric('')">＋ 新建指标</button></h2>
    <table><thead><tr><th style="width:130px">指标</th><th>构成（分子 ÷ 分母）</th>
      <th style="width:100px">关联数据表</th><th style="width:100px">统计时间字段</th>
      <th style="width:80px">状态</th><th style="width:130px">操作</th></tr></thead>
    <tbody id="metricRows"></tbody></table>

    <div id="metricEditor" style="display:none;margin-top:14px" class="form">
      <div style="width:100%;display:grid;grid-template-columns:130px 1fr;
        gap:8px 12px;align-items:center">
        <span class="hint" style="width:auto">指标名</span>
        <span><input id="mName" size="12">
          <input id="mAliases" placeholder="别名，逗号分隔" size="18"></span>
        <span class="hint" style="width:auto">指标描述</span>
        <input id="mDef" style="width:90%">
        <span class="hint" style="width:auto">关联的数据表</span>
        <select id="mTable" style="max-width:200px"></select>
        <span class="hint" style="width:auto">统计时间字段</span>
        <span><select id="mTime" style="max-width:200px"></select>
          <span class="hint" style="width:auto;margin-left:8px">
            引用矩阵语义角色；跨表指标要求两表都有绑定</span></span>
        <span class="hint" style="width:auto">filter 表达式</span>
        <input id="mFilter" placeholder="公共过滤，作用于分子与分母，可留空"
          style="width:90%">
      </div>
      <div style="width:100%;display:grid;grid-template-columns:1fr 1fr;gap:12px;
        margin-top:8px">
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
          <div style="color:var(--ok);font-size:12px;margin-bottom:6px"><b>分子</b></div>
          <input id="mNumExpr" placeholder="聚合表达式，如 SUM(order_amt)"
            style="width:95%">
          <input id="mNumDesc" placeholder="分子描述" style="width:95%;margin-top:6px">
        </div>
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px">
          <div style="color:var(--warn);font-size:12px;margin-bottom:6px">
            <b>分母</b> <span class="hint" style="width:auto">可选，留空即单一聚合</span></div>
          <input id="mDenExpr" placeholder="聚合表达式，如 COUNT(*)" style="width:95%">
          <input id="mDenDesc" placeholder="分母描述" style="width:95%;margin-top:6px">
        </div>
      </div>
      <div style="width:100%;display:flex;gap:10px;align-items:center;margin-top:8px">
        <label style="font-size:12px"><input type="checkbox" id="mVerified"> 已确认</label>
        <label style="font-size:12px"><input type="checkbox" id="mRestricted"> 受限</label>
        试算区间 <input id="mStart" value="2026-06-01" size="9">
        ~ <input id="mEnd" value="2026-06-30" size="9">
        <button onclick="trialMetric()">试算</button>
        <span id="trialOut" style="font-size:12px;color:var(--ok)"></span>
        <span style="flex:1"></span>
        <button class="primary" onclick="saveMetric()">保存（产生新版本）</button>
        <button onclick="$('metricEditor').style.display='none'">取消</button>
      </div>
    </div>

    <div id="historyPanel" style="display:none;margin-top:14px">
      <h2>版本历史 · <span id="hName"></span></h2>
      <table><thead><tr><th>版本</th><th>修改人</th><th>时间</th><th>内容</th></tr></thead>
      <tbody id="historyRows"></tbody></table>
    </div>
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
      '<button onclick="browseMetadata(\\''+s.source_id+'\\')">元数据</button> '+
      '<button onclick="testSource(\\''+s.source_id+'\\')">集成测试</button> '+
      (s.active?'':'<button onclick="activateSource(\\''+s.source_id+
        '\\')">激活</button> ')+
      '<button onclick="bootstrapSource(this,\\''+s.source_id+
        '\\')">全库冷启动</button></td></tr>';
  }).join('')||'<tr><td colspan="4" class="empty">尚未接入数据源</td></tr>';
}
let metaSource='';
async function browseMetadata(id){
  const r=await fetch('/admin/sources/'+id+'/metadata',{headers:H()});
  if(!r.ok){ toast('✗ 拉取元数据失败：'+r.status,false); return; }
  const d=await r.json(); metaSource=id;
  $('metaSrcId').textContent=id;
  $('metaRows').innerHTML=d.tables.map(t=>
    '<tr><td><input type="checkbox" class="metaSel" value="'+t.name+'"></td>'+
    '<td><b>'+t.name+'</b><br><small style="color:var(--muted)">'+t.database+
    '</small></td><td>'+(t.row_count==null?'—':t.row_count.toLocaleString())+
    '</td><td><details><summary>'+t.columns.length+' 列</summary>'+
    '<div class="mono">'+t.columns.map(c=>c.name+'  '+c.type+
      (c.comment?('  -- '+c.comment):'')).join('\\n')+'</div></details></td></tr>'
  ).join('')||'<tr><td colspan="4" class="empty">无表</td></tr>';
  $('metaBrowser').style.display='block';
  $('metaBrowser').scrollIntoView({behavior:'smooth'});
}
async function integrateTables(){
  const tables=[...document.querySelectorAll('.metaSel:checked')].map(i=>i.value);
  if(!tables.length){ toast('请勾选至少一张表', false); return; }
  const r=await fetch('/admin/sources/'+metaSource+'/integrate',
    {method:'POST',headers:H(),body:JSON.stringify({tables})});
  const d=await r.json();
  if(!r.ok){ toast('✗ '+(d.detail||r.status), false); return; }
  toast('✓ 集成完成\\n实体草稿 '+d.entities_created.length+' 个 · 指标草稿 '+
    d.metrics_drafted.length+' 个\\n确认队列 +'+d.confirmations_queued+
    ' · profiling '+d.profiled_columns+' 列\\n→ 去语义层/确认队列查看归一结果');
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

let metricCache={}, entityCache={}, matrixTables=[], pickerCtx=null;

async function loadSemantic(){
  const [entities, metrics] = await Promise.all([
    fetch('/admin/semantic/objects?kind=entity',{headers:H()}).then(r=>r.json()),
    fetch('/admin/semantic/objects?kind=metric',{headers:H()}).then(r=>r.json()),
  ]);
  entityCache={}; entities.forEach(e=>entityCache[e.name]=e);
  metricCache={}; metrics.forEach(m=>metricCache[m.name]=m);
  const sources=await(await fetch('/admin/sources',{headers:H()})).json();
  const active=sources.find(s=>s.active);
  matrixTables=[];
  if(active){
    const meta=await(await fetch('/admin/sources/'+active.source_id+'/metadata',
      {headers:H()})).json();
    matrixTables=meta.tables;
  }
  renderMatrix(); renderMetrics();
}

function chipHtml(text, frozen){
  return '<span class="mono" style="background:var(--panel2);border:0.5px solid '+
    'var(--border);border-radius:6px;padding:2px 7px;font-size:11.5px">'+
    (frozen?'❄ ':'')+text+'</span>';
}
function plusHtml(onclick){
  return '<button style="border-style:dashed;padding:2px 10px;font-size:12px" '+
    'onclick="'+onclick+'">＋</button>';
}

function renderMatrix(){
  const tables=matrixTables.map(t=>t.name);
  $('matrixHead').innerHTML='<tr><th style="width:190px">语义对象</th>'+
    tables.map(t=>{
      const tt=matrixTables.find(x=>x.name===t);
      return '<th>'+t+'<br><small style="color:var(--muted);font-weight:400">'+
        (tt.row_count==null?'':tt.row_count.toLocaleString()+' 行')+'</small></th>';
    }).join('')+'</tr>';
  const onlyUnbound=$('onlyUnbound').checked;
  let html='';
  for(const name of Object.keys(entityCache)){
    const e=entityCache[name], p=e.payload;
    const frozen=new Set(p.frozen_bindings||[]);
    html+='<tr><td colspan="'+(tables.length+1)+'" style="background:var(--panel2)">'+
      '<b>▣ 实体 · '+name+'</b> <small style="color:var(--muted)">别名：'+
      ((p.aliases||[]).join(' / ')||'—')+' · v'+e.version+'</small>'+
      '<span style="float:right"><button style="font-size:11px;padding:2px 8px" '+
      'onclick="showHistory(\'entity\',\''+name+'\')">历史</button></span></td></tr>';
    // 主键行（bindings）
    const bindCells=tables.map(t=>{
      const b=(p.bindings||[]).find(x=>x.table===t);
      if(b) return '<td>'+chipHtml(b.expr?('ƒ '+b.expr):b.column,
        frozen.has(b.table+'.'+b.column))+'</td>';
      return '<td>'+plusHtml('openPicker(\''+name+'\',\'binding\',\''+t+'\')')+
        '</td>';
    });
    const anyUnbound=(p.bindings||[]).length<tables.length;
    if(!onlyUnbound || anyUnbound)
      html+='<tr><td><b>'+(p.canonical_key||'ID')+'</b>'+
        '<span style="font-size:10.5px;color:var(--accent);margin-left:5px">主键</span>'+
        '</td>'+bindCells.join('')+'</tr>';
    // 语义角色行（按角色名分组）
    const roles={};
    (p.semantic_roles||[]).forEach(r=>{(roles[r.role]=roles[r.role]||[]).push(r);});
    for(const role of Object.keys(roles)){
      const cells=tables.map(t=>{
        const r=roles[role].find(x=>x.table===t);
        if(r) return '<td>'+chipHtml(r.column,false)+'</td>';
        return '<td>'+plusHtml('openPicker(\''+name+'\',\'role:'+role+
          '\',\''+t+'\')')+'</td>';
      });
      if(!onlyUnbound || roles[role].length<tables.length)
        html+='<tr><td>'+role+'<span style="font-size:10.5px;color:var(--muted);'+
          'margin-left:5px">语义角色</span></td>'+cells.join('')+'</tr>';
    }
    // 枚举行（只读展示）
    for(const em of (p.enum_mappings||[])){
      const cells=tables.map(t=>{
        const hit=Object.keys(em.mappings||{}).find(k=>k.startsWith(t+'.'));
        if(!hit) return '<td style="color:var(--muted);text-align:center">—</td>';
        const pairs=Object.entries(em.mappings[hit]).map(([k,v])=>k+'='+v).join(' ');
        return '<td>'+chipHtml(hit.split('.')[1],false)+
          '<div style="font-size:10.5px;color:var(--muted);margin-top:2px">'+
          pairs+'</div></td>';
      });
      if(!onlyUnbound)
        html+='<tr><td>'+em.concept+'<span style="font-size:10.5px;'+
          'color:var(--muted);margin-left:5px">枚举</span></td>'+cells.join('')+'</tr>';
    }
  }
  $('matrixBody').innerHTML=html||'<tr><td class="empty" colspan="9">'+
    '暂无实体——可"新建实体"或经数据源集成产出</td></tr>';
}

function newEntity(){
  const name=prompt('实体名（如：客户）'); if(!name) return;
  const key=prompt('主键概念名（canonical key，如：customer_id）')||'id';
  fetch('/admin/semantic/entities/'+encodeURIComponent(name),{method:'PUT',
    headers:H(),body:JSON.stringify({canonical_key:key,aliases:[],bindings:[],
    join_paths:[],enum_mappings:[],semantic_roles:[]})}).then(()=>loadSemantic());
}
function newRole(){
  const ent=prompt('挂在哪个实体下？（'+Object.keys(entityCache).join(' / ')+'）');
  if(!ent||!entityCache[ent]){ if(ent) toast('实体不存在',false); return; }
  const role=prompt('语义角色名（如：支付日期）'); if(!role) return;
  toast('已创建角色「'+role+'」——在矩阵行中点击 ＋ 绑定各表的列');
  const p=entityCache[ent].payload;
  p.semantic_roles=p.semantic_roles||[];
  p.semantic_roles.push({table:'__pending__',column:'__pending__',role});
  putEntity(ent,p);
}

function openPicker(entity, kind, table){
  pickerCtx={entity,kind,table};
  const label=kind==='binding'?(entityCache[entity].payload.canonical_key+'（主键）')
    :kind.slice(5)+'（语义角色）';
  $('pickerTitle').textContent='绑定 · '+entity+' / '+label+' → 表 '+table;
  const cols=(matrixTables.find(t=>t.name===table)||{columns:[]}).columns;
  $('pickerCols').innerHTML=cols.map(c=>
    '<button style="font-size:12px" onclick="saveColBinding(\''+c.name+'\')">'+
    c.name+' <small style="color:var(--muted)">'+c.type+'</small></button>').join('')
    ||'<span class="hint">该表无列信息</span>';
  $('exprSourceCol').innerHTML='<option value="">（预览对照列，可选）</option>'+
    cols.map(c=>'<option>'+c.name+'</option>').join('');
  $('exprInput').value=''; $('exprPreview').textContent='';
  pickerMode('col');
  $('cellPicker').style.display='flex';
  $('cellPicker').scrollIntoView({behavior:'smooth'});
}
function pickerMode(m){
  $('pickerCols').style.display=m==='col'?'flex':'none';
  $('pickerExpr').style.display=m==='expr'?'block':'none';
  $('tabCol').className=m==='col'?'primary':'';
  $('tabExpr').className=m==='expr'?'primary':'';
}
async function previewExpr(){
  const r=await fetch('/admin/semantic/bindings/preview',{method:'POST',headers:H(),
    body:JSON.stringify({table:pickerCtx.table,expr:$('exprInput').value,
      source_column:$('exprSourceCol').value})});
  const d=await r.json();
  $('exprPreview').textContent=r.ok?
    ('✓ 方言校验通过\n预览：\n'+d.rows.map(row=>row.join('  →  ')).join('\n')):
    ('✗ '+(d.detail||r.status));
}
function saveColBinding(col){ applyBinding(col, ''); }
function saveExprBinding(){
  const expr=$('exprInput').value.trim();
  if(!expr){ toast('表达式不能为空',false); return; }
  applyBinding($('exprSourceCol').value||'', expr);
}
function applyBinding(col, expr){
  const {entity,kind,table}=pickerCtx;
  const p=entityCache[entity].payload;
  if(kind==='binding'){
    p.bindings=(p.bindings||[]).filter(b=>b.table!==table);
    p.bindings.push({table,column:col,grain:'',expr});
  } else {
    const role=kind.slice(5);
    p.semantic_roles=(p.semantic_roles||[]).filter(r=>
      !(r.role===role&&(r.table===table||r.table==='__pending__')));
    p.semantic_roles.push({table,column:expr||col,role});
  }
  putEntity(entity,p);
  $('cellPicker').style.display='none';
}
async function putEntity(name,payload){
  const r=await fetch('/admin/semantic/entities/'+encodeURIComponent(name),
    {method:'PUT',headers:H(),body:JSON.stringify(payload)});
  const d=await r.json();
  toast(r.ok?('✓ 已保存 '+name+'（v'+d.version+'）'):('✗ '+(d.detail||r.status)),r.ok);
  loadSemantic();
}

function renderMetrics(){
  $('metricRows').innerHTML=Object.keys(metricCache).map(name=>{
    const m=metricCache[name], p=m.payload;
    const num=p.numerator, den=p.denominator;
    const comp=num?('<span class="mono">'+num.expr+'</span>'+
      (den?(' ÷ <span class="mono">'+den.expr+'</span>'):'')+
      (num.filter?('<div style="font-size:10.5px;color:var(--muted)">filter：'+
        num.filter+'</div>'):'')):
      ('<span class="mono">'+(p.expr||'—')+'</span>'+
       '<div style="font-size:10.5px;color:var(--muted)">旧式表达式</div>');
    return '<tr><td><b>'+name+'</b><br><small style="color:var(--muted)">'+
      ((p.aliases||[]).join('/')||'')+' v'+m.version+'</small></td>'+
      '<td>'+comp+'</td>'+
      '<td>'+(num&&num.table?('<span class="mono">'+num.table+'</span>'):'—')+'</td>'+
      '<td>'+(p.time_field?('<span class="badge b-dim">'+p.time_field+'</span>'):'—')+
      '</td><td>'+(p.verified?'<span class="badge b-ok">✓ 已确认</span>'
        :'<span class="badge b-warn">草稿</span>')+
      (p.restricted?' <span class="badge b-err">受限</span>':'')+'</td>'+
      '<td><button onclick="editMetric(\''+name+'\')">编辑</button> '+
      '<button onclick="showHistory(\'metric\',\''+name+'\')">历史</button>'+
      '</td></tr>';
  }).join('')||'<tr><td colspan="6" class="empty">暂无指标</td></tr>';
}

function collectRoles(){
  const roles=new Set();
  Object.values(entityCache).forEach(e=>
    (e.payload.semantic_roles||[]).forEach(r=>roles.add(r.role)));
  return [...roles];
}
function editMetric(name){
  const p=name?metricCache[name].payload:{};
  $('mName').value=name||''; $('mDef').value=p.definition||'';
  $('mAliases').value=(p.aliases||[]).join(',');
  $('mTable').innerHTML=matrixTables.map(t=>'<option>'+t.name+'</option>').join('');
  $('mTime').innerHTML='<option value="">（无时间口径）</option>'+
    collectRoles().map(r=>'<option>'+r+'</option>').join('');
  const num=p.numerator||{};
  $('mTable').value=num.table||($('mTable').options[0]?.value||'');
  $('mTime').value=p.time_field||'';
  $('mFilter').value=num.filter||'';
  $('mNumExpr').value=num.expr||p.expr||'';
  $('mNumDesc').value=num.description||'';
  $('mDenExpr').value=(p.denominator||{}).expr||'';
  $('mDenDesc').value=(p.denominator||{}).description||'';
  $('mVerified').checked=!!p.verified; $('mRestricted').checked=!!p.restricted;
  $('trialOut').textContent='';
  $('metricEditor').style.display='flex';
  $('metricEditor').scrollIntoView({behavior:'smooth'});
}
function buildMetricBody(){
  const table=$('mTable').value, filter=$('mFilter').value.trim();
  const body={
    name:$('mName').value.trim(),
    definition:$('mDef').value.trim(),
    aliases:$('mAliases').value.split(',').map(s=>s.trim()).filter(Boolean),
    time_field:$('mTime').value,
    numerator:{expr:$('mNumExpr').value.trim(),
      description:$('mNumDesc').value.trim(), table, filter},
    verified:$('mVerified').checked, restricted:$('mRestricted').checked,
  };
  if($('mDenExpr').value.trim())
    body.denominator={expr:$('mDenExpr').value.trim(),
      description:$('mDenDesc').value.trim(), table, filter};
  return body;
}
async function trialMetric(){
  const r=await fetch('/admin/semantic/metrics/trial',{method:'POST',headers:H(),
    body:JSON.stringify({metric:buildMetricBody(),
      start:$('mStart').value,end:$('mEnd').value})});
  const d=await r.json();
  if(!r.ok){ $('trialOut').style.color='var(--err)';
    $('trialOut').textContent='✗ '+(d.detail||r.status); return; }
  $('trialOut').style.color='var(--ok)';
  const num=d.numerator_value==null?'—':(+d.numerator_value).toLocaleString();
  $('trialOut').textContent='✓ '+num+
    (d.denominator_value!=null?(' ÷ '+(+d.denominator_value).toLocaleString()+
      ' = '+(d.ratio*100).toFixed(1)+'%'):'');
}
async function saveMetric(){
  const body=buildMetricBody();
  if(!body.name){ toast('指标名不能为空',false); return; }
  const r=await fetch('/admin/semantic/metrics/'+encodeURIComponent(body.name),
    {method:'PUT',headers:H(),body:JSON.stringify(body)});
  const d=await r.json();
  toast(r.ok?('✓ 已保存 '+body.name+'（v'+d.version+'）')
    :('✗ '+(d.detail||r.status)),r.ok);
  if(r.ok){ $('metricEditor').style.display='none'; loadSemantic(); }
}
async function showHistory(kind,name){
  const rows=await(await fetch('/admin/semantic/history?kind='+kind+
    '&name='+encodeURIComponent(name),{headers:H()})).json();
  $('hName').textContent=name;
  $('historyRows').innerHTML=rows.reverse().map(r=>
    '<tr><td>v'+r.version+'</td><td>'+r.updated_by+'</td><td class="mono">'+
    r.updated_at.slice(0,19)+'</td><td><details><summary>查看</summary>'+
    '<div class="mono">'+JSON.stringify(r.payload,null,2).replace(/</g,'&lt;')+
    '</div></details></td></tr>').join('');
  $('historyPanel').style.display='block';
  $('historyPanel').scrollIntoView({behavior:'smooth'});
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
