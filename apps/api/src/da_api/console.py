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
  .rowHi td { background:rgba(77,163,255,.08); }
  .pickerRow td { padding:0 8px 8px; }
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
    <h2>待办 <span class="sub" style="display:inline;margin:0">
      治理收件箱——每项一键直达，处理完回到这里</span></h2>
    <div id="todoList"></div>
    <h2 style="margin-top:22px">运行统计</h2>
    <div class="cards" id="statCards"></div>
  </section>

  <section id="sources">
    <div id="sopBar" class="form" style="gap:4px;padding:10px 16px"></div>
    <div id="opResult" style="display:none;margin-bottom:16px" class="form"></div>
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
      <button onclick="toggleForm('entityForm')">＋ 新建实体</button>
      <button onclick="toggleForm('roleForm')">＋ 新建属性（语义角色）</button>
      <label style="font-size:12px;color:var(--muted)">
        <input type="checkbox" id="onlyUnbound" onchange="renderMatrix()"> 仅看未绑定</label>
    </div>
    <div id="entityForm" class="form" style="display:none">
      <input id="neName" placeholder="实体名，如：客户" size="12">
      <input id="neKey" placeholder="主键概念名，如：customer_id" size="16">
      <button class="primary" onclick="createEntity()">创建</button>
      <button onclick="toggleForm('entityForm')">取消</button>
      <span class="hint">创建后在矩阵行中逐格绑定各表的物理列</span>
    </div>
    <div id="roleForm" class="form" style="display:none">
      <select id="nrEntity" style="max-width:160px"></select>
      <input id="nrRole" placeholder="语义角色名，如：支付日期" size="14">
      <button class="primary" onclick="createRole()">创建</button>
      <button onclick="toggleForm('roleForm')">取消</button>
      <span class="hint">角色行出现后点击 ＋ 绑定各表的列；指标的统计时间字段引用它</span>
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
        <button onclick="closePicker()">关闭</button>
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
      <div id="metricErrBox" style="width:100%;display:none;background:var(--panel2);
        border:1px solid var(--err);border-radius:6px;padding:8px 12px;
        font-size:12px"></div>
      <div id="verifyPanel" style="width:100%;display:none;border-top:1px solid
        var(--border);padding-top:10px;margin-top:4px">
        <b style="font-size:12px;color:var(--accent)">试一问验证</b>
        <span class="hint" style="width:auto;margin-left:8px">
          以当前操作员权限发起真实问答，验证口径生效</span>
        <div style="display:flex;gap:8px;margin-top:8px">
          <input id="verifyQ" style="flex:1">
          <button class="primary" onclick="verifyAsk()">提问</button>
        </div>
        <pre id="verifyOut" class="mono" style="margin-top:8px;font-size:12px;
          white-space:pre-wrap;color:var(--text)"></pre>
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

function todoRow(n, color, title, sub, btnLabel, target, primary=true){
  return '<div style="display:flex;align-items:center;gap:12px;background:'+
    'var(--panel);border:1px solid var(--border);border-radius:8px;'+
    'padding:10px 14px;margin-bottom:8px">'+
    '<span style="font-size:18px;font-weight:700;min-width:26px;'+
    'text-align:center;color:'+color+'">'+n+'</span>'+
    '<span style="flex:1">'+title+
    '<small style="display:block;color:var(--muted);margin-top:1px">'+sub+
    '</small></span>'+
    '<button '+(primary?'class="primary" ':'')+'onclick="go(\\''+target+
    '\\')">'+btnLabel+'</button></div>';
}

async function loadOverview(){
  const [o, t]=await Promise.all([
    fetch('/admin/overview?'+tenantQ(),{headers:H()}).then(r=>r.json()),
    fetch('/admin/todos',{headers:H()}).then(r=>r.json()),
  ]);
  let todos='';
  if(!t.sop.has_source)
    todos+=todoRow('!','var(--accent)','开始接入第一个数据源',
      '上传数据集或连接 CK/Hive，接入向导会引导完成全部步骤',
      '开始接入','sources');
  if(t.confirmations.count)
    todos+=todoRow(t.confirmations.count,'var(--warn)','待确认口径',
      t.confirmations.top||'确认队列有待处理项','去处理','confirm');
  if(t.drift.count)
    todos+=todoRow(t.drift.count,'var(--err)','漂移告警：绑定已冻结',
      t.drift.items.join('；')+(t.drift.affected_metrics.length?
        '——受影响指标：'+t.drift.affected_metrics.join('、'):''),
      '去修复','semantic');
  if(t.draft_metrics.count)
    todos+=todoRow(t.draft_metrics.count,'var(--muted)','草稿指标未确认',
      t.draft_metrics.names.join('、')+'——试算通过并确认后才是可信口径',
      '去试算','semantic',false);
  $('todoList').innerHTML=todos||
    '<div class="empty">没有待办 🎉 语义层健康，去对话页看看效果吧</div>';

  const acc=o.accuracy==null?'—':Math.round(o.accuracy*100)+'%';
  $('statCards').innerHTML=[
    ['数据源',o.sources+(o.active_source?'（'+o.active_source+'）':'')],
    ['业务实体',o.entities],['指标口径',o.metrics],
    ['已验证答案',o.verified_answers],['会话数',o.sessions],
    ['活跃用户',o.users],['审计事件',o.audit_events],['准确率',acc],
  ].map(([k,v])=>'<div class="stat"><div class="k">'+k+
    '</div><div class="v">'+v+'</div></div>').join('');
}

function toggleForm(id){
  const el=$(id);
  el.style.display=el.style.display==='none'?'flex':'none';
}
function showResult(html){
  const box=$('opResult');
  box.innerHTML=html; box.style.display='flex';
  box.scrollIntoView({behavior:'smooth'});
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
function renderSop(t, hasSources){
  const steps=[
    ['连接数据源', hasSources],
    ['集成体检', hasSources],
    ['选表集成 / 冷启动', t.sop.has_semantics],
    ['确认口径', t.sop.has_semantics && t.sop.confirm_clear],
    ['试问验证', false],
  ];
  let cur=steps.findIndex(s=>!s[1]); if(cur<0) cur=steps.length-1;
  $('sopBar').innerHTML='<span style="font-size:12px;color:var(--muted);'+
    'margin-right:6px">接入 SOP</span>'+steps.map(([label,done],i)=>{
    const state=done?'done':(i===cur?'cur':'');
    const dot=done?'✓':(i+1);
    return '<span style="display:flex;align-items:center;gap:6px;font-size:12px">'+
      '<span style="width:20px;height:20px;border-radius:50%;display:flex;'+
      'align-items:center;justify-content:center;font-size:11px;'+
      (done?'background:rgba(63,185,80,.15);color:var(--ok)':
       i===cur?'background:var(--accent);color:#fff':
       'background:var(--panel2);color:var(--muted)')+'">'+dot+'</span>'+
      '<span style="color:'+(i===cur?'var(--text)':'var(--muted)')+'">'+label+
      '</span></span>'+(i<steps.length-1?
      '<span style="width:22px;height:1px;background:'+
      (done?'var(--ok)':'var(--border)')+';margin:0 4px"></span>':'');
  }).join('');
}

async function loadSources(){
  renderSrcFields();
  const [list, t]=await Promise.all([
    fetch('/admin/sources',{headers:H()}).then(r=>r.json()),
    fetch('/admin/todos',{headers:H()}).then(r=>r.json()),
  ]);
  renderSop(t, list.length>0);
  $('srcRows').innerHTML=list.map(s=>{
    const status=s.active?'<span class="badge b-ok">使用中</span>'
      :'<span class="badge b-dim">已接入</span>';
    return '<tr><td><b>'+s.source_id+'</b></td><td>'+s.kind+'</td><td>'+status+
      '</td><td>'+
      '<button onclick="browseMetadata(\\''+s.source_id+'\\')">元数据</button> '+
      '<button onclick="testSource(\\''+s.source_id+'\\')">集成测试</button> '+
      (s.active?'':'<button onclick="activateSource(this,\\''+s.source_id+
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
function integrationResultCard(title, d){
  return '<div style="width:100%"><b style="font-size:13px;color:var(--ok)">✓ '+
    title+'</b></div>'+
    '<span class="badge b-dim">实体草稿 '+d.entities_created.length+'</span>'+
    '<span class="badge b-dim">指标草稿 '+d.metrics_drafted.length+'</span>'+
    '<span class="badge b-warn">确认题 +'+d.confirmations_queued+'</span>'+
    '<span class="badge b-dim">profiling '+d.profiled_columns+' 列</span>'+
    '<span style="flex:1"></span>'+
    (d.confirmations_queued?
      '<button class="primary" onclick="go(\\'confirm\\')">去确认口径 →</button>':
      '<button class="primary" onclick="go(\\'semantic\\')">查看语义层 →</button>')+
    '<button onclick="$(\\'opResult\\').style.display=\\'none\\'">关闭</button>';
}
async function integrateTables(){
  const tables=[...document.querySelectorAll('.metaSel:checked')].map(i=>i.value);
  if(!tables.length){ toast('请勾选至少一张表', false); return; }
  showResult('<b style="font-size:13px">集成中… profiling '+tables.length+
    ' 张表，证据图归一…</b>');
  const r=await fetch('/admin/sources/'+metaSource+'/integrate',
    {method:'POST',headers:H(),body:JSON.stringify({tables})});
  const d=await r.json();
  if(!r.ok){ showResult('<b style="color:var(--err)">✗ 集成失败：'+
    (d.detail||r.status)+'</b><button onclick="integrateTables()">重试</button>');
    return; }
  showResult(integrationResultCard('集成完成（'+tables.join(' / ')+'）', d));
}
async function testSource(id){
  const r=await(await fetch('/admin/sources/'+id+'/test',
    {method:'POST',headers:H()})).json();
  toast((r.passed?'✓ 体检通过\\n':'✗ 体检未通过\\n')+
    r.checks.map(c=>(c.ok?'✓ ':'✗ ')+c.name+'：'+c.detail).join('\\n'), r.passed);
}
async function activateSource(btn,id){
  // 风险分级：换源影响所有用户的查询目标 → 二次确认（就地，不用原生弹窗）
  if(btn.dataset.armed!=='1'){
    btn.dataset.armed='1';
    btn.textContent='确认切换？影响所有用户';
    btn.style.borderColor='var(--warn)'; btn.style.color='var(--warn)';
    setTimeout(()=>{ btn.dataset.armed=''; btn.textContent='激活';
      btn.style.borderColor=''; btn.style.color=''; }, 4000);
    return;
  }
  await fetch('/admin/sources/'+id+'/activate',{method:'POST',headers:H()});
  toast('✓ 已切换到 '+id); loadSources(); }
async function bootstrapSource(btn,id){
  btn.disabled=true; btn.textContent='冷启动中…';
  showResult('<b style="font-size:13px">冷启动中… 挖掘查询日志 → profiling → '+
    '证据图归一（全库）</b>');
  const r=await(await fetch('/admin/sources/'+id+'/bootstrap',
    {method:'POST',headers:H()})).json();
  btn.disabled=false; btn.textContent='全库冷启动';
  showResult(integrationResultCard('冷启动完成 · '+id, r));
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
  $('nrEntity').innerHTML=Object.keys(entityCache).map(n=>
    '<option>'+n+'</option>').join('')||'<option value="">（先创建实体）</option>';
  renderMatrix(); renderMetrics();
}

function chipHtml(text, frozen){
  return '<span class="mono" style="background:var(--panel2);border:0.5px solid '+
    'var(--border);border-radius:6px;padding:2px 7px;font-size:11.5px">'+
    (frozen?'❄ ':'')+text+'</span>';
}
function plusHtml(entity, kind, table){
  return '<button style="border-style:dashed;padding:2px 10px;font-size:12px" '+
    'data-e="'+entity+'" data-k="'+kind+'" data-t="'+table+'" '+
    'onclick="openPicker(this)">＋</button>';
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
      'onclick="showHistory(\\'entity\\',\\''+name+'\\')">历史</button></span></td></tr>';
    // 主键行（bindings）
    const bindCells=tables.map(t=>{
      const b=(p.bindings||[]).find(x=>x.table===t);
      if(b) return '<td>'+chipHtml(b.expr?('ƒ '+b.expr):b.column,
        frozen.has(b.table+'.'+b.column))+'</td>';
      return '<td>'+plusHtml(name,'binding',t)+'</td>';
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
        return '<td>'+plusHtml(name,'role:'+role,t)+'</td>';
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

async function createEntity(){
  const name=$('neName').value.trim(), key=$('neKey').value.trim()||'id';
  if(!name){ toast('实体名不能为空',false); return; }
  await fetch('/admin/semantic/entities/'+encodeURIComponent(name),{method:'PUT',
    headers:H(),body:JSON.stringify({canonical_key:key,aliases:[],bindings:[],
    join_paths:[],enum_mappings:[],semantic_roles:[]})});
  toggleForm('entityForm'); $('neName').value=''; $('neKey').value='';
  toast('✓ 实体「'+name+'」已创建——在矩阵行中逐格绑定');
  loadSemantic();
}
function createRole(){
  const ent=$('nrEntity').value, role=$('nrRole').value.trim();
  if(!ent||!entityCache[ent]){ toast('请先创建实体',false); return; }
  if(!role){ toast('角色名不能为空',false); return; }
  const p=entityCache[ent].payload;
  p.semantic_roles=p.semantic_roles||[];
  if(p.semantic_roles.some(r=>r.role===role && r.table!=='__pending__')){
    toast('角色已存在',false); return; }
  p.semantic_roles.push({table:'__pending__',column:'__pending__',role});
  toggleForm('roleForm'); $('nrRole').value='';
  toast('✓ 角色「'+role+'」已创建——在角色行点击 ＋ 绑定各表的列');
  putEntity(ent,p);
}

function openPicker(el){
  const entity=el.dataset.e, kind=el.dataset.k, table=el.dataset.t;
  pickerCtx={entity,kind,table};
  const label=kind==='binding'?(entityCache[entity].payload.canonical_key+'（主键）')
    :kind.slice(5)+'（语义角色）';
  $('pickerTitle').textContent='绑定 · '+entity+' / '+label+' → 表 '+table;
  const cols=(matrixTables.find(t=>t.name===table)||{columns:[]}).columns;
  $('pickerCols').innerHTML=cols.map(c=>
    '<button style="font-size:12px" onclick="saveColBinding(\\''+c.name+'\\')">'+
    c.name+' <small style="color:var(--muted)">'+c.type+'</small></button>').join('')
    ||'<span class="hint">该表无列信息</span>';
  $('exprSourceCol').innerHTML='<option value="">（预览对照列，可选）</option>'+
    cols.map(c=>'<option>'+c.name+'</option>').join('');
  $('exprInput').value=''; $('exprPreview').textContent='';
  pickerMode('col');
  // 锚定单元格：选择器作为被点行正下方的展开行，保持空间上下文
  closePicker();
  const tr=el.closest('tr'); tr.classList.add('rowHi');
  const nrow=document.createElement('tr'); nrow.className='pickerRow';
  const td=document.createElement('td'); td.colSpan=tr.children.length;
  td.appendChild($('cellPicker'));
  nrow.appendChild(td); tr.after(nrow);
  $('cellPicker').style.display='flex';
  nrow.scrollIntoView({behavior:'smooth', block:'center'});
}
function closePicker(){
  $('cellPicker').style.display='none';
  document.body.appendChild($('cellPicker'));
  document.querySelectorAll('.pickerRow').forEach(r=>r.remove());
  document.querySelectorAll('.rowHi').forEach(r=>r.classList.remove('rowHi'));
}
function jumpToBind(role, table){
  go('semantic');
  setTimeout(()=>{
    const btn=document.querySelector(
      '[data-k="role:'+role+'"][data-t="'+table+'"]');
    if(btn){ openPicker(btn); }
    else { toggleForm('roleForm'); $('nrRole').value=role;
      toast('角色「'+role+'」尚未创建——先在此创建，再绑定表 '+table, false); }
  }, 400);
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
    ('✓ 方言校验通过\\n预览：\\n'+d.rows.map(row=>row.join('  →  ')).join('\\n')):
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
  closePicker();
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
      '<td><button onclick="editMetric(\\''+name+'\\')">编辑</button> '+
      '<button onclick="showHistory(\\'metric\\',\\''+name+'\\')">历史</button>'+
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
function showMetricErr(msg){
  // 报错即动作：解析"请先在映射矩阵为表 X 绑定语义角色 Y"→ 附 [去绑定] 直达按钮
  const box=$('metricErrBox');
  let html='✗ '+msg;
  const re=/为表 (\S+) 绑定语义角色 (\S+?)($|；)/g;
  let m;
  while((m=re.exec(msg))!==null){
    html+=' <button style="font-size:11px;padding:2px 10px" '+
      'onclick="jumpToBind(\\''+m[2]+'\\',\\''+m[1]+'\\')">去绑定 '+m[2]+
      ' → '+m[1]+'</button>';
  }
  box.innerHTML=html; box.style.display='block';
}
async function trialMetric(){
  $('metricErrBox').style.display='none';
  const r=await fetch('/admin/semantic/metrics/trial',{method:'POST',headers:H(),
    body:JSON.stringify({metric:buildMetricBody(),
      start:$('mStart').value,end:$('mEnd').value})});
  const d=await r.json();
  if(!r.ok){ $('trialOut').textContent='';
    showMetricErr(d.detail||r.status); return; }
  $('trialOut').style.color='var(--ok)';
  const num=d.numerator_value==null?'—':(+d.numerator_value).toLocaleString();
  $('trialOut').textContent='✓ '+num+
    (d.denominator_value!=null?(' ÷ '+(+d.denominator_value).toLocaleString()+
      ' = '+(d.ratio*100).toFixed(1)+'%'):'');
}
async function saveMetric(){
  $('metricErrBox').style.display='none';
  const body=buildMetricBody();
  if(!body.name){ toast('指标名不能为空',false); return; }
  const r=await fetch('/admin/semantic/metrics/'+encodeURIComponent(body.name),
    {method:'PUT',headers:H(),body:JSON.stringify(body)});
  const d=await r.json();
  if(!r.ok){ showMetricErr(d.detail||r.status); return; }
  toast('✓ 已保存 '+body.name+'（v'+d.version+'）');
  loadSemantic();
  // 动线 C · 就地验证：保存完成态直接给"试一问"，当场看新口径生效
  $('verifyQ').value=body.name+'是多少？';
  $('verifyOut').textContent='';
  $('verifyPanel').style.display='block';
  $('verifyPanel').scrollIntoView({behavior:'smooth', block:'center'});
}
async function verifyAsk(){
  $('verifyOut').textContent='提问中…（真实问答，以当前操作员权限执行）';
  const sid='console-verify-'+Math.random().toString(36).slice(2,8);
  const r=await fetch('/sessions/'+sid+'/turns',{method:'POST',headers:H(),
    body:JSON.stringify({question:$('verifyQ').value})});
  const d=await r.json();
  if(!r.ok){ $('verifyOut').textContent='✗ '+(d.detail||r.status); return; }
  const hits=d.matched_metrics||[];
  const head=hits.length?('【指标直连 ✓ '+hits.join('、')+'——口径已生效】\\n\\n')
    :('【未命中指标直连：检查指标名/别名是否覆盖该问法】\\n\\n');
  $('verifyOut').textContent=head+d.answer;
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
