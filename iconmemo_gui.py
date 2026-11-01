#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图标记忆 图形界面
====================
不依赖第三方库：只用 Python 标准库起一个仅监听 127.0.0.1 的本地服务，
用浏览器作为界面（档案管理 + 快照历史/缩略图 + 差异可视化 + 显示器拓扑）。

安全设计：
  * 只绑定 127.0.0.1，不对外网开放
  * 每次启动生成随机 token，所有写操作必须携带，防其他网页 CSRF
  * 校验 Host 头

Copyright (c) 2026 Kiter
SPDX-License-Identifier: MIT
"""

import http.server
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iconmemo as E  # noqa: E402

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

TOKEN = secrets.token_urlsafe(18)
DEFAULT_PORT = 8765

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>图标记忆 · 桌面布局管理</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#f5f6f8; --panel:#ffffff; --line:#e3e6ea; --line2:#eef0f3;
  --text:#1b1f24; --muted:#6b7280; --accent:#2563eb; --accent-d:#1d4ed8;
  --ok:#0f9d58; --warn:#d97706; --danger:#dc2626; --chip:#f1f3f5;
  --radius:10px;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:14px/1.5 "Microsoft YaHei UI","Segoe UI",system-ui,-apple-system,sans-serif;}
.wrap{max-width:1240px;margin:0 auto;padding:20px 22px 44px}
header{display:flex;align-items:center;gap:14px;margin-bottom:16px}
.logo{width:38px;height:38px;border-radius:9px;background:linear-gradient(140deg,#3b82f6,#1d4ed8);
  display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:15px;
  box-shadow:0 2px 8px rgba(37,99,235,.28)}
h1{font-size:18px;margin:0;font-weight:650;letter-spacing:.2px}
h1 small{font-weight:400;color:var(--muted);font-size:12px;margin-left:8px}
.spacer{flex:1}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}
.env{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:1px;background:var(--line2)}
.env div{padding:12px 16px;background:var(--panel)}
.env .k{font-size:11px;color:var(--muted);letter-spacing:.4px;margin-bottom:3px}
.env .v{font-size:15px;font-weight:600}
.env .v.good{color:var(--ok)} .env .v.bad{color:var(--warn)}
.topo{display:flex;align-items:center;gap:12px;padding:11px 16px;border-top:1px solid var(--line2);
  background:#fbfcfd;flex-wrap:wrap}
.topo .lbl{font-size:11px;color:var(--muted);letter-spacing:.4px}
.topo .mon{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid var(--line);
  border-radius:7px;padding:3px 10px;font-size:12.5px}
.topo .mon b{font-weight:650}
.topo .mon .pm{background:#e8f0fe;color:var(--accent);border-radius:4px;padding:0 5px;font-size:10px;font-weight:600}
.cols{display:grid;grid-template-columns:1.02fr .98fr;gap:16px;margin-top:16px;align-items:start}
@media(max-width:940px){.cols{grid-template-columns:1fr}}
.colstack>*+*{margin-top:16px}
h2{font-size:13px;margin:0;font-weight:650;color:#374151;display:flex;align-items:center;gap:8px}
h2 .cnt{background:var(--chip);border-radius:20px;padding:1px 9px;font-size:11px;color:var(--muted);font-weight:500}
.head{display:flex;align-items:center;padding:13px 16px 9px}
.head .spacer{flex:1}
.tag{font-size:11px;background:#ffedd5;color:#9a3412;border:1px solid #fed7aa;
  border-radius:5px;padding:2px 8px;font-weight:600}
.list{max-height:330px;overflow:auto;padding:4px 6px 8px}
.row{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:8px;border:1px solid transparent}
.row:hover{background:#fafbfc;border-color:var(--line2)}
.row+.row{margin-top:2px}
.row .nm{font-weight:600;font-size:13px;word-break:break-all}
.row .meta{font-size:11.5px;color:var(--muted);margin-top:2px}
.row .bd{flex:1;min-width:0}
.thumb{width:88px;height:52px;flex:0 0 auto;border:1px solid var(--line2);border-radius:6px;background:#fbfcfd}
.cur{display:inline-block;font-size:10px;padding:1px 7px;border-radius:5px;
  background:#e8f0fe;color:var(--accent);font-weight:600;margin-left:6px;vertical-align:1px}
.delta{display:inline-block;font-size:10px;padding:1px 6px;border-radius:5px;margin-left:6px;
  background:#fef3c7;color:#92400e;font-weight:600;vertical-align:1px}
.delta.same{background:var(--chip);color:var(--muted)}
button{font:inherit;border:1px solid var(--line);background:#fff;color:var(--text);
  padding:6px 12px;border-radius:7px;cursor:pointer;transition:.12s;white-space:nowrap}
button:hover{background:#f7f8fa;border-color:#d0d5dc}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button.primary:hover{background:var(--accent-d)}
button.danger:hover{color:var(--danger);border-color:#fca5a5;background:#fef2f2}
button.sm{padding:4px 9px;font-size:12.5px}
.bar{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}
canvas{width:100%;height:auto;display:block;border-radius:8px;background:#fbfcfd;border:1px solid var(--line2)}
.preview{padding:12px}
.empty{color:var(--muted);text-align:center;padding:30px 10px;font-size:13px;line-height:1.8}
.cmpbar{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff7ed;
  border-top:1px solid #fed7aa;flex-wrap:wrap}
.cmpbar .txt{flex:1;min-width:200px;font-size:12.5px;color:#7c2d12;line-height:1.6}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;
  vertical-align:-1px;border:1px solid transparent}
#toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(14px);
  background:#1b1f24;color:#fff;padding:10px 18px;border-radius:9px;font-size:13px;
  opacity:0;pointer-events:none;transition:.22s;max-width:78vw;z-index:99;
  box-shadow:0 6px 20px rgba(0,0,0,.22)}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.err{background:#b91c1c}
.hint{font-size:11.5px;color:var(--muted);margin-top:9px;line-height:1.65}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">DS</div>
    <h1>图标记忆<small>桌面图标布局保存 / 恢复 · 快照历史 · 差异可视化</small></h1>
    <div class="spacer"></div>
    <button id="btnInfo">图标清单</button>
    <button id="btnReload">刷新</button>
    <button id="btnQuit" title="结束程序进程">退出程序</button>
  </header>

  <div class="card">
    <div class="env" id="env"></div>
    <div class="topo" id="topo"></div>
  </div>

  <div class="bar">
    <button class="primary" id="btnSave">保存当前布局</button>
    <button id="btnSaveAs">另存为…</button>
    <button id="btnUndo">撤销上次恢复</button>
    <button id="btnDir">打开数据目录</button>
  </div>

  <div class="cols">
    <div class="colstack">
      <div class="card">
        <div class="head"><h2>布局档案 <span class="cnt" id="layCnt">0</span></h2></div>
        <div class="list" id="layouts"><div class="empty">加载中…</div></div>
      </div>
      <div class="card">
        <div class="head">
          <h2>历史快照 <span class="cnt" id="snapCnt">0</span></h2>
          <div class="spacer"></div>
          <button class="sm" id="btnSnap">立即拍一张</button>
        </div>
        <div class="list" id="snapshots"></div>
      </div>
    </div>

    <div class="card">
      <div class="head">
        <h2>桌面预览 <span class="cnt" id="icoCnt">0</span></h2>
        <div class="spacer"></div>
        <span class="tag" id="cmpTag" style="display:none">对比模式</span>
      </div>
      <div class="preview" style="padding-top:6px"><canvas id="cv" width="1000" height="560"></canvas></div>
      <div class="preview" style="padding-top:0">
        <div class="hint" id="prevHint"></div>
        <div class="legend" id="legend" style="display:none">
          <span><i style="background:#dbe3ee"></i>不动的图标</span>
          <span><i style="background:#f97316;border-color:#ea580c"></i>会移动的图标（当前位置）</span>
          <span><i style="background:#dc2626;border-color:#b91c1c"></i>恢复后的目标位置</span>
          <span><i style="background:#94a3b8"></i>新增图标（无当前位置）</span>
        </div>
      </div>
      <div class="cmpbar" id="cmpPanel" style="display:none">
        <div class="txt" id="cmpText"></div>
        <button class="primary" id="btnConfirm">确认恢复</button>
        <button id="btnCancel">取消</button>
      </div>
    </div>
  </div>

  <div class="hint">
    提示：桌面右键 →「查看」→ 关闭「自动排列图标」，恢复后的布局重启也能保持稳定。
    图标记忆 恢复时会自动关闭该选项。<br>
    想要「桌面布局版本历史」，可运行 <b>命令行.bat watch --snapshot-every 30</b>
    让程序常驻并每 30 分钟自动留存一张快照。
  </div>
</div>
<div id="toast"></div>

<script>
const TK = "__TOKEN__";
let STATE = null;
let CMP = null;          // 当前对比：{file, origin, moves, willMove, ...}

function toast(msg, err){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = err ? 'on err' : 'on';
  clearTimeout(t._h);
  t._h = setTimeout(function(){ t.className=''; }, err?5200:2800);
}
async function api(path, body){
  const opt = { headers: { 'X-Token': TK } };
  if (body !== undefined){
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  const j = await r.json().catch(function(){ return {ok:false, error:'响应解析失败'}; });
  if (!j.ok) throw new Error(j.error || '操作失败');
  return j;
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

/* ---------- 缩略图：直接用快照里的坐标现画，不存图片 ---------- */
const CW = 111, CH = 152;             // 桌面图标单元尺寸
function fitTransform(pts, W, H, pad){
  let maxX = CW, maxY = CH;
  pts.forEach(function(p){ maxX = Math.max(maxX, p[0]+CW); maxY = Math.max(maxY, p[1]+CH); });
  const k = Math.min((W-pad*2)/maxX, (H-pad*2)/maxY);
  return {k:k, ox:(W-maxX*k)/2, oy:(H-maxY*k)/2};
}
function drawThumb(cv, pts){
  const W = 88, H = 52;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W*dpr; cv.height = H*dpr;
  cv.style.width = W+'px'; cv.style.height = H+'px';
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle = '#fbfcfd'; ctx.fillRect(0,0,W,H);
  if (!pts || !pts.length) return;
  const t = fitTransform(pts, W, H, 3);
  ctx.fillStyle = '#b9c6d8';
  const w = Math.max(1.6, CW*t.k*0.62), h = Math.max(1.6, CH*t.k*0.42);
  pts.forEach(function(p){ ctx.fillRect(t.ox+p[0]*t.k, t.oy+p[1]*t.k, w, h); });
}

/* ---------- 主预览 / 差异可视化 ---------- */
function renderPreview(){
  const s = STATE;
  if (!s) return;
  const cv = document.getElementById('cv');
  const ctx = cv.getContext('2d');
  const icons = s.icons;
  document.getElementById('icoCnt').textContent = icons.length;
  const W = 1000, H = 560;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W*dpr; cv.height = H*dpr; cv.style.height = 'auto';
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle = '#fbfcfd'; ctx.fillRect(0,0,W,H);

  const cmp = CMP;
  const legend = document.getElementById('legend');
  const tag = document.getElementById('cmpTag');
  legend.style.display = cmp ? 'flex' : 'none';
  tag.style.display = cmp ? 'inline-block' : 'none';

  if (!icons.length){
    ctx.fillStyle='#9aa2ac'; ctx.font='13px "Microsoft YaHei UI",sans-serif';
    ctx.textAlign='center'; ctx.fillText('没有读取到桌面图标', W/2, H/2);
    document.getElementById('prevHint').textContent = '';
    return;
  }

  // 目标位置映射
  const toMap = {};
  if (cmp && cmp.moves) cmp.moves.forEach(function(m){ toMap[m.name] = m.to; });

  // 画布范围要同时容纳当前位置与目标位置
  const allPts = icons.map(function(i){ return [i.x, i.y]; });
  if (cmp && cmp.moves) cmp.moves.forEach(function(m){ allPts.push(m.to); });
  const t = fitTransform(allPts, W, H, 14);

  function box(x, y, fill, stroke, lw, dash){
    const bx = t.ox + x*t.k, by = t.oy + y*t.k;
    const bw = CW*t.k*0.72, bh = CH*t.k*0.62;
    const px = bx + (CW*t.k - bw)/2, py = by + (CH*t.k - bh)/2;
    ctx.save();
    ctx.setLineDash(dash || []);
    ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = lw;
    const r = 4;
    ctx.beginPath();
    ctx.moveTo(px+r, py);
    ctx.arcTo(px+bw, py, px+bw, py+bh, r);
    ctx.arcTo(px+bw, py+bh, px, py+bh, r);
    ctx.arcTo(px, py+bh, px, py, r);
    ctx.arcTo(px, py, px+bw, py, r);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.restore();
    return [px+bw/2, py+bh/2];
  }

  const fontSize = Math.max(7, Math.min(11, t.k*13))+'px "Microsoft YaHei UI",sans-serif';

  // 第一遍：当前位置
  icons.forEach(function(ic, n){
    const isMoving = cmp && toMap[ic.name] !== undefined;
    if (!cmp){
      box(ic.x, ic.y, n % 2 ? '#e8eefc' : '#eef2f7', '#dbe3ee', 1);
    } else if (isMoving){
      box(ic.x, ic.y, '#fdba74', '#ea580c', 1.6);
    } else {
      box(ic.x, ic.y, '#eef1f5', '#e3e8ef', 1);
    }
  });

  // 第二遍：目标位置 + 连线
  if (cmp && cmp.moves){
    ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 1.4;
    ctx.setLineDash([4,3]);
    cmp.moves.forEach(function(m){
      if (!m.from) return;
      const ax = t.ox + (m.from[0]+CW/2)*t.k, ay = t.oy + (m.from[1]+CH/2)*t.k;
      const bx = t.ox + (m.to[0]+CW/2)*t.k,   by = t.oy + (m.to[1]+CH/2)*t.k;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    });
    ctx.setLineDash([]);
    cmp.moves.forEach(function(m){
      box(m.to[0], m.to[1], 'rgba(220,38,38,.14)', '#dc2626', 1.8,
          m.from ? [] : [3,3]);
    });
    // 名称：只对会移动的图标标注，且数量可控时
    if (cmp.moves.length <= 40){
      ctx.font = fontSize; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#7f1d1d';
      cmp.moves.forEach(function(m){
        const c = t.ox + (m.to[0]+CW/2)*t.k, cy = t.oy + (m.to[1]+CH/2)*t.k;
        ctx.fillText((m.name||'').slice(0,6), c, cy, CW*t.k*0.9);
      });
    }
  } else {
    ctx.font = fontSize; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = '#5b6673';
    icons.forEach(function(ic, n){
      const c = t.ox + (ic.x+CW/2)*t.k, cy = t.oy + (ic.y+CH/2)*t.k;
      const nm = ic.name || '?';
      ctx.fillText(nm.slice(0,5), c, cy, CW*t.k*0.9);
    });
  }

  document.getElementById('prevHint').textContent = cmp
    ? '对比模式：共 ' + cmp.moves.length + ' 个图标会移动 · 红框为恢复后的目标位置'
    : '按实际坐标等比绘制 · 共 ' + icons.length + ' 个图标';
}

function renderEnv(s){
  const e = s.env;
  const items = [
    ['主屏分辨率', e.screen],
    ['缩放', e.dpi + ' DPI (' + Math.round(e.dpi/96*100) + '%)'],
    ['显示器', e.monitors + ' 屏'],
    ['桌面图标', s.icons.length + ' 个'],
    ['自动排列', e.auto_arrange ? '开启' : '关闭', e.auto_arrange ? 'bad' : 'good'],
    ['对齐网格', e.snap_to_grid ? '开启' : '关闭'],
  ];
  document.getElementById('env').innerHTML = items.map(function(it){
    return '<div><div class="k">'+it[0]+'</div><div class="v '+(it[2]||'')+'">'+esc(it[1])+'</div></div>';
  }).join('');
}

function renderTopo(s){
  const e = s.env;
  const mons = e.monitor_list || [];
  let html = '<span class="lbl">显示器拓扑</span>';
  if (mons.length){
    html += mons.map(function(m,i){
      return '<span class="mon"><b>'+String.fromCharCode(65+i)+'</b> '+
        m.rect[2]+'×'+m.rect[3]+
        (m.dpi!==96 ? ' <span style="color:#6b7280">'+m.dpi+'dpi</span>' : '')+
        (m.primary ? ' <span class="pm">主屏</span>' : '')+
        '</span>';
    }).join('');
  } else {
    html += '<span class="mon">'+esc(e.screen)+'</span>';
  }
  html += '<span class="lbl" style="margin-left:auto">虚拟桌面 '+e.virtual_desktops+' 个</span>';
  html += '<span class="lbl">档案键 '+esc(s.profile_key)+'</span>';
  document.getElementById('topo').innerHTML = html;
}

function renderLayouts(s){
  const box = document.getElementById('layouts');
  document.getElementById('layCnt').textContent = s.layouts.length;
  if (!s.layouts.length){
    box.innerHTML = '<div class="empty">还没有保存过布局<br>点下方「保存当前布局」建第一个</div>';
    return;
  }
  box.innerHTML = s.layouts.map(function(l){
    return '<div class="row">'+
      '<div class="bd"><div class="nm">'+esc(l.title)+
        (l.is_current?'<span class="cur">当前分辨率</span>':'')+'</div>'+
      '<div class="meta">'+esc(l.saved_at)+' · '+l.icon_count+' 个图标 · '+esc(l.profile)+'</div></div>'+
      '<button class="sm" data-restore="'+esc(l.file)+'">对比并恢复</button>'+
      '<button class="sm danger" data-del="'+esc(l.file)+'">删除</button>'+
    '</div>';
  }).join('');
  box.querySelectorAll('[data-restore]').forEach(function(b){
    b.onclick = function(){ startCompare(b.getAttribute('data-restore'), 'layout'); };
  });
  box.querySelectorAll('[data-del]').forEach(function(b){
    b.onclick = async function(){
      if (!confirm('删除档案「'+b.getAttribute('data-del')+'」？')) return;
      try { await api('/api/delete', {file:b.getAttribute('data-del')}); toast('已删除'); load(); }
      catch(e){ toast(e.message, true); }
    };
  });
}

function renderSnapshots(s){
  const box = document.getElementById('snapshots');
  const rows = s.snapshots || [];
  document.getElementById('snapCnt').textContent = s.snapshot_total;
  if (!rows.length){
    box.innerHTML = '<div class="empty">还没有快照<br>点「立即拍一张」留个版本，或让程序常驻定时留存</div>';
    return;
  }
  box.innerHTML = rows.map(function(r, i){
    let d = '';
    if (r.changed === 0) d = '<span class="delta same">无变化</span>';
    else if (r.changed > 0) d = '<span class="delta">Δ'+r.changed+' 个变化</span>';
    const note = r.note ? '「'+esc(r.note)+'」 · ' : '';
    const ts = esc(r.saved_at.slice(0,19).replace('T',' '));
    return '<div class="row">'+
      '<canvas class="thumb" data-thumb="'+i+'"></canvas>'+
      '<div class="bd"><div class="nm">'+ts+(i===0?'<span class="cur">最新</span>':'')+d+'</div>'+
      '<div class="meta">'+note+r.icon_count+' 个图标 · '+esc(r.profile)+'</div></div>'+
      '<button class="sm" data-rb="'+esc(r.file)+'">对比并回滚</button>'+
    '</div>';
  }).join('');
  rows.forEach(function(r, i){
    const cv = box.querySelector('[data-thumb="'+i+'"]');
    if (cv) drawThumb(cv, r.thumb);
  });
  box.querySelectorAll('[data-rb]').forEach(function(b){
    b.onclick = function(){ startCompare(b.getAttribute('data-rb'), 'snapshot'); };
  });
}

/* ---------- 对比预检 ---------- */
async function startCompare(file, origin){
  try{
    const p = await api('/api/precheck', {file:file, from:origin});
    CMP = {file:file, origin:origin, moves:p.move_detail || [],
           willMove:p.will_move || 0, unchanged:p.unchanged || 0,
           matched:p.matched, missing:p.missing || [], extra:p.extra || [],
           diffs:p.topology_diffs || [], scaled:p.scaled,
           factor:p.scale_factor, oob:p.out_of_bounds};
    const box = document.getElementById('cmpPanel');
    let txt = '<b>将移动 ' + CMP.willMove + ' 个图标</b>（' + CMP.unchanged + ' 个已在正确位置）';
    if (CMP.diffs.length) txt += '　⚠ ' + CMP.diffs.join('；');
    if (CMP.scaled) txt += '　将按 ' + CMP.factor[0].toFixed(3) + '×/' + CMP.factor[1].toFixed(3) + ' 缩放';
    if (CMP.oob) txt += '　' + CMP.oob + ' 个图标会超出桌面范围';
    if (CMP.missing.length) txt += '<br>档案中已不存在：' + esc(CMP.missing.slice(0,5).join('、'))
                                  + (CMP.missing.length>5 ? ' 等 '+CMP.missing.length+' 个' : '');
    if (CMP.extra.length) txt += '<br>桌面新增未收录：' + esc(CMP.extra.slice(0,5).join('、'))
                                + (CMP.extra.length>5 ? ' 等 '+CMP.extra.length+' 个' : '');
    if (!CMP.willMove) txt = '布局完全一致，无需恢复。<br>' + txt;
    document.getElementById('cmpText').innerHTML = txt;
    box.style.display = 'flex';
    renderPreview();
    box.scrollIntoView({block:'nearest', behavior:'smooth'});
  }catch(e){ toast(e.message, true); }
}

function exitCompare(){
  CMP = null;
  document.getElementById('cmpPanel').style.display = 'none';
  renderPreview();
}

async function load(){
  try{
    const s = await api('/api/state');
    STATE = s;
    renderEnv(s); renderTopo(s); renderLayouts(s); renderSnapshots(s); renderPreview();
  }catch(e){ toast(e.message, true); }
}

document.getElementById('btnConfirm').onclick = async function(){
  if (!CMP) return;
  const c = CMP;
  this.disabled = true;
  try{
    if (c.origin === 'snapshot'){
      await api('/api/rollback', {file:c.file});
      toast('已回滚到该快照');
    } else {
      await api('/api/restore', {file:c.file});
      toast('恢复完成');
    }
    exitCompare();
    load();
  }catch(e){ toast(e.message, true); }
  finally { this.disabled = false; }
};
document.getElementById('btnCancel').onclick = function(){
  exitCompare();
  document.getElementById('prevHint').textContent = '已取消，未做任何改动';
};

document.getElementById('btnReload').onclick = function(){ exitCompare(); load(); };
document.getElementById('btnInfo').onclick = async function(){
  try{
    const s = await api('/api/state');
    const txt = s.icons.map(function(i){
      return '#'+String(i.index).padStart(3,'0')+
        '  ('+String(i.x).padStart(5)+','+String(i.y).padStart(5)+')  '+i.name;
    }).join('\n');
    const w = window.open('', '_blank');
    w.document.write('<meta charset="utf-8"><title>桌面图标清单</title>'+
      '<pre style="font:13px/1.6 Consolas,monospace;padding:20px">'+
      '桌面图标清单（共 '+s.icons.length+' 个）\n\n'+txt.replace(/[&<>]/g,'')+'</pre>');
    w.document.close();
  }catch(e){ toast(e.message,true); }
};
document.getElementById('btnSnap').onclick = async function(){
  const n = prompt('给这张快照写个备注（可留空）：', '');
  if (n === null) return;
  try{
    const r = await api('/api/snapshot', {note:n});
    toast(r.skipped ? '布局与上一张快照相同，未重复保存' : '快照已保存');
    load();
  }catch(e){ toast(e.message, true); }
};
document.getElementById('btnSave').onclick = async function(){
  try{ const r = await api('/api/save', {}); toast('已保存：'+r.file); load(); }
  catch(e){ toast(e.message, true); }
};
document.getElementById('btnSaveAs').onclick = async function(){
  const n = prompt('给这个布局起个名字：', '我的布局');
  if (!n) return;
  try{ const r = await api('/api/save', {name:n}); toast('已保存：'+r.file); load(); }
  catch(e){ toast(e.message, true); }
};
document.getElementById('btnUndo').onclick = async function(){
  if (!confirm('撤销上一次恢复操作？')) return;
  try{ await api('/api/undo', {}); toast('已撤销'); load(); }
  catch(e){ toast(e.message, true); }
};
document.getElementById('btnDir').onclick = async function(){
  try{ await api('/api/open', {}); toast('已打开数据目录'); }
  catch(e){ toast(e.message, true); }
};
document.getElementById('btnQuit').onclick = async function(){
  if (!confirm('确定退出 图标记忆？\n（界面会失效，程序进程将结束）')) return;
  try{ await api('/api/quit', {}); }catch(e){}
  document.body.innerHTML =
    '<div style="padding:80px 20px;text-align:center;font:15px/2 ' +
    '\'Microsoft YaHei UI\',sans-serif;color:#6b7280">' +
    '图标记忆 已退出<br><span style="font-size:13px">可以关闭此页面了</span></div>';
};
load();
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "IconMemo"

    def log_message(self, fmt, *args):
        pass

    # ---------- 工具 ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _guard(self) -> bool:
        host = (self.headers.get("Host") or "")
        if not (host.startswith("127.0.0.1") or host.startswith("localhost")):
            self._json({"ok": False, "error": "非法 Host"}, 403)
            return False
        return True

    def _auth(self) -> bool:
        if self.headers.get("X-Token") != TOKEN:
            self._json({"ok": False, "error": "令牌无效，请刷新页面"}, 403)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path == "/api/state":
            try:
                self._json({"ok": True, **build_state()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if not self._guard() or not self._auth():
            return
        path = urllib.parse.urlparse(self.path).path
        data = self._body()
        try:
            if path == "/api/save":
                name = (data.get("name") or "").strip() or None
                p = E.do_save(name, quiet=True)
                self._json({"ok": True, "file": os.path.basename(p)})
            elif path == "/api/snapshot":
                p = E.do_snapshot((data.get("note") or "").strip(), quiet=True)
                self._json({"ok": True, "skipped": p is None,
                            "file": os.path.basename(p) if p else None})
            elif path == "/api/precheck":
                f = _resolve(data.get("file"), data.get("from"))
                r = E.do_restore(f, dry_run=True, quiet=True)
                self._json({"ok": True, "matched": r["matched"],
                            "total": len(r["missing"]) + r["matched"],
                            "missing": r["missing"], "extra": r["extra"],
                            "topology_diffs": r["topology_diffs"],
                            "scaled": r["scaled"], "scale_factor": r["scale_factor"],
                            "out_of_bounds": r["out_of_bounds"],
                            "move_detail": r["move_detail"], "will_move": r["will_move"],
                            "unchanged": r["unchanged"]})
            elif path == "/api/restore":
                f = _resolve(data.get("file"), data.get("from"))
                E.do_restore(f, quiet=True)
                self._json({"ok": True})
            elif path == "/api/rollback":
                f = _resolve(data.get("file"), "snapshot")
                E.do_rollback(f, quiet=True)
                self._json({"ok": True})
            elif path == "/api/undo":
                E.do_undo(quiet=True)
                self._json({"ok": True})
            elif path == "/api/delete":
                target = os.path.join(E.layouts_dir(),
                                      os.path.basename(data.get("file") or ""))
                if os.path.exists(target):
                    os.remove(target)
                self._json({"ok": True})
            elif path == "/api/open":
                os.startfile(E.data_dir())  # noqa: S606
                self._json({"ok": True})
            elif path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


# ---------- 数据 ----------
def build_state() -> dict:
    env = E.environment()
    hLV, _, _ = E.find_desktop_listview()
    icons = E.read_icons(hLV, with_names=True) if hLV else []
    cur_key = E.profile_key(env)

    layouts = []
    d = E.layouts_dir()
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        p = os.path.join(d, f)
        try:
            doc = E.read_archive(p)
        except Exception:
            continue
        prof = doc.get("profile", "")
        label = doc.get("label") or ""
        if label:
            title = label
        elif prof == cur_key:
            title = "主档案（当前分辨率）"
        else:
            title = f
        layouts.append({
            "file": f, "title": title, "profile": prof,
            "saved_at": doc.get("saved_at", "?"),
            "icon_count": doc.get("icon_count", 0),
            "is_current": prof == cur_key and not label,
            "mtime": os.path.getmtime(p),
        })
    layouts.sort(key=lambda x: x["mtime"], reverse=True)
    for l in layouts:
        l.pop("mtime", None)

    return {
        "env": env,
        "profile_key": cur_key,
        "icons": icons,
        "layouts": layouts,
        "snapshots": E.list_snapshots(limit=40, with_thumb=True),
        "snapshot_total": len([f for f in os.listdir(E.snapshots_dir())
                               if f.endswith(".json")]),
        "backups": len([f for f in os.listdir(E.backups_dir()) if f.endswith(".json")]),
    }


def _resolve(fname: str | None, origin: str | None = None):
    """前端只传文件名，这里按来源目录还原成安全路径"""
    if not fname:
        return None
    if os.path.isabs(fname) and os.path.exists(fname):
        return fname
    base = E.snapshots_dir() if origin == "snapshot" else E.layouts_dir()
    return os.path.join(base, os.path.basename(fname))


def _free_port(start: int) -> int:
    for p in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def main():
    port = _free_port(DEFAULT_PORT)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print("  图标记忆 已启动")
    print(f"  界面地址： {url}")
    print("  关闭这个窗口即退出程序")
    print("=" * 56)
    if not os.environ.get("ICONMEMO_NOBROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
