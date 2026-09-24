/* 管网漏点隔离规划台 — 原生 JS 前端 */
"use strict";

const state = {
  nodes: [],
  segments: [],
  valves: [],
  result: null,
  layout: new Map(), // nodeId -> {x,y}
};

const ROLE_TEXT = {
  inlet: "进水点",
  leak: "漏点",
  must_keep: "必保供水点",
  normal: "普通点",
};
const STATUS_TEXT = {
  inlet_supply: "进水点(供水)",
  supplied: "有供水",
  isolated: "漏点已隔离",
  leak_not_isolated: "漏点未隔离!",
  disconnected: "断水",
  flowing: "通水",
  closed: "已关阀",
  dead: "停水",
};
const SOLVER_TEXT = { base: "常规", failover: "单阀备援" };
const KIND_TEXT = {
  no_base_plan: "原本就没有常规可行方案",
  no_failover_plan: "仅缺少备援方案(存在常规可行方案)",
};

// 备援情形: 全关阀清单
function valveListTable(valveIds) {
  if (!valveIds.length) return "<p>无需关闭任何阀门。</p>";
  const valveCost = new Map(state.valves.map((v) => [v.id, v.cost]));
  return `
    <table><thead><tr><th>顺序</th><th>阀门编号</th><th>绑定管段</th><th>关闭代价</th></tr></thead>
    <tbody>${valveIds.map((id, k) => {
      const v = state.valves.find((x) => x.id === id) || {};
      return `<tr><td>${k + 1}</td><td><b>${esc(id)}</b></td>
      <td>${esc(v.segment_id || "")}</td><td>${esc(valveCost.get(id) ?? "")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

function nodeStatusTable(nodeStatus) {
  return `
    <table><thead><tr><th>节点</th><th>角色</th><th>状态</th><th>连通的进水点</th></tr></thead>
    <tbody>${nodeStatus.map((n) => `
      <tr><td>${esc(n.id)}${n.label && n.label !== n.id ? ` <span class="role-name">(${esc(n.label)})</span>` : ""}</td>
      <td>${ROLE_TEXT[n.role]}</td>
      <td><span class="tag ${n.status}">${STATUS_TEXT[n.status]}</span></td>
      <td>${n.connected_inlets.length ? esc(n.connected_inlets.join(", ")) : "—"}</td></tr>`).join("")}
    </tbody></table>`;
}

function segmentStatusTable(segmentStatus) {
  return `
    <table><thead><tr><th>管段</th><th>端点</th><th>绑定阀门</th><th>状态</th></tr></thead>
    <tbody>${segmentStatus.map((s) => `
      <tr><td>${esc(s.id)}</td><td>${esc(s.endpoint_a)} — ${esc(s.endpoint_b)}</td>
      <td>${s.valve_id ? esc(s.valve_id) : "—"}</td>
      <td><span class="tag ${s.status}">${STATUS_TEXT[s.status]}</span></td></tr>`).join("")}
    </tbody></table>`;
}

// 一种执行情形(正常 / 某阀失效)的校验结果行
function scenarioCheckRows(checks) {
  return checks.map((chk) => {
    const name = chk.scenario === "normal"
      ? "正常执行(全部按计划关闭)"
      : `阀门 ${esc(chk.failed_valve)} 关不上` +
        (chk.segment_id ? ` <span class="role-name">(管段 ${esc(chk.segment_id)} 保持通水)</span>` : "");
    const leakCell = chk.leaks_isolated
      ? '<span class="tag supplied">全部断开</span>'
      : `<span class="tag leak_not_isolated">仍通水: ${esc(chk.leaks_reachable.join(", "))}</span>`;
    const keepCell = chk.keeps_supplied
      ? '<span class="tag supplied">全部有供水</span>'
      : `<span class="tag disconnected">断水: ${esc(chk.keeps_starved.join(", "))}</span>`;
    const okCell = chk.ok
      ? '<span class="tag supplied">通过</span>'
      : '<span class="tag leak_not_isolated">不通过</span>';
    return `<tr><td>${name}</td><td>${esc(chk.effective_closed_valves.join(", ") || "—")}</td>
    <td>${leakCell}</td><td>${keepCell}</td><td>${okCell}</td></tr>`;
  }).join("");
}

const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- API ----------
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opt);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) {
    const errs = data && data.detail && data.detail.errors;
    throw new Error(errs ? errs.join("\n") : `请求失败 (${resp.status})`);
  }
  return data;
}

function payloadFromUI() {
  return {
    nodes: state.nodes
      .filter((n) => n.id.trim())
      .map((n) => ({ id: n.id.trim(), role: n.role, label: n.label.trim() })),
    segments: state.segments
      .filter((s) => s.id.trim())
      .map((s) => ({
        id: s.id.trim(),
        endpoint_a: s.endpoint_a.trim(),
        endpoint_b: s.endpoint_b.trim(),
      })),
    valves: state.valves
      .filter((v) => v.id.trim())
      .map((v) => ({ id: v.id.trim(), segment_id: v.segment_id.trim(), cost: v.cost })),
  };
}

// ---------- 渲染输入表格 ----------
function nodeOptions(selected) {
  return state.nodes.map((n) =>
    `<option value="${esc(n.id)}"${n.id === selected ? " selected" : ""}>${esc(n.id)}</option>`
  ).join("");
}
function segmentOptions(selected) {
  return state.segments.map((s) =>
    `<option value="${esc(s.id)}"${s.id === selected ? " selected" : ""}>${esc(s.id)}</option>`
  ).join("");
}

function renderNodes() {
  const filter = $("#node-role-filter").value;
  const rows = state.nodes
    .map((n, i) => ({ n, i }))
    .filter(({ n }) => !filter || n.role === filter);
  $("#table-nodes tbody").innerHTML = rows.map(({ n, i }) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(n.id)}" placeholder="如 S1"></td>
      <td><select data-f="role">
        ${Object.entries(ROLE_TEXT).map(([v, t]) =>
          `<option value="${v}"${n.role === v ? " selected" : ""}>${t}</option>`).join("")}
      </select></td>
      <td><input data-f="label" value="${esc(n.label)}" placeholder="可选"></td>
      <td class="act"><button data-del="node" title="删除">✕</button></td>
    </tr>`).join("");
}

function renderSegments() {
  $("#table-segments tbody").innerHTML = state.segments.map((s, i) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(s.id)}" placeholder="如 P1"></td>
      <td><select data-f="endpoint_a"><option value=""></option>${nodeOptions(s.endpoint_a)}</select></td>
      <td><select data-f="endpoint_b"><option value=""></option>${nodeOptions(s.endpoint_b)}</select></td>
      <td class="act"><button data-del="segment" title="删除">✕</button></td>
    </tr>`).join("");
}

function renderValves() {
  $("#table-valves tbody").innerHTML = state.valves.map((v, i) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(v.id)}" placeholder="如 V1"></td>
      <td><select data-f="segment_id"><option value=""></option>${segmentOptions(v.segment_id)}</select></td>
      <td><input data-f="cost" type="number" min="1" step="1" value="${esc(v.cost)}"></td>
      <td class="act"><button data-del="valve" title="删除">✕</button></td>
    </tr>`).join("");
}

function renderAll() {
  renderNodes();
  renderSegments();
  renderValves();
  drawCanvas();
}

// ---------- 表格事件(委托) ----------
function bindTable(table, key, defaults) {
  table.addEventListener("input", (e) => {
    const td = e.target.closest("tr");
    if (!td) return;
    const i = Number(td.dataset.i);
    const f = e.target.dataset.f;
    if (f === undefined) return;
    let val = e.target.value;
    if (f === "cost") val = Number(val);
    state[key][i][f] = val;
    // 节点编号变化会影响管段下拉; 阀门绑定影响仅在保存时校验
    if (key === "nodes") renderSegments();
    drawCanvas();
  });
  table.addEventListener("change", () => {
    if (key === "nodes") renderSegments();
    drawCanvas();
  });
  table.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-del]");
    if (!btn) return;
    const i = Number(btn.closest("tr").dataset.i);
    state[key].splice(i, 1);
    renderAll();
  });
}

document.querySelectorAll(".add-row").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.dataset.table;
    if (t === "nodes") state.nodes.push({ id: "", role: "normal", label: "" });
    if (t === "segments") state.segments.push({ id: "", endpoint_a: "", endpoint_b: "" });
    if (t === "valves") {
      if (state.valves.length >= 14) {
        showErrors(["阀门数量最多 14 个"]);
        return;
      }
      state.valves.push({ id: "", segment_id: "", cost: 1 });
    }
    renderAll();
  });
});

bindTable($("#table-nodes"), "nodes");
bindTable($("#table-segments"), "segments");
bindTable($("#table-valves"), "valves");
$("#node-role-filter").addEventListener("change", renderNodes);

// ---------- 结果渲染 ----------
function showErrors(errors) {
  const box = $("#input-errors");
  box.innerHTML = `<b>输入存在问题:</b><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
  box.classList.remove("hidden");
}
function clearErrors() {
  $("#input-errors").classList.add("hidden");
}

function renderResult(r) {
  state.result = r;
  const banner = $("#result-banner");
  banner.classList.remove("hidden", "ok", "bad");
  const body = $("#result-body");

  if (!r.feasible) {
    renderInfeasible(r, banner, body);
    drawCanvas();
    return;
  }

  if (r.solver === "failover") {
    renderFailover(r, banner, body);
    drawCanvas();
    return;
  }

  banner.classList.add("ok");
  banner.innerHTML =
    `<span class="big">✓ 已求得最优方案</span>${esc(r.message)}` +
    `<br><small>共枚举 ${r.subset_count} 个阀门组合, 其中 ${r.isolating_subset_count} 个可隔离全部漏点。</small>`;

  body.innerHTML = `
    <div class="card">
      <h3>关阀清单（${r.closed_valves.length} 个, 总代价 ${r.total_cost}）</h3>
      ${valveListTable(r.closed_valves)}
    </div>

    <div class="card">
      <h3>各节点隔离后供水状态</h3>
      ${nodeStatusTable(r.node_status)}
    </div>

    <div class="card">
      <h3>各管段状态</h3>
      ${segmentStatusTable(r.segment_status)}
    </div>`;
  drawCanvas();
}

function renderInfeasible(r, banner, body) {
  banner.classList.add("bad");
  let head = "无解";
  if (r.solver === "failover") {
    head = r.infeasible_kind === "no_failover_plan"
      ? "无备援方案"
      : "备援无解(常规规划本身无解)";
  }
  banner.innerHTML = `<span class="big">${head}</span>${esc(r.message)}`;
  if (r.reason_detail && r.reason_detail.witness_path) {
    banner.innerHTML += `<br><small>证据通路: ${esc(r.reason_detail.witness_path)}</small>`;
  }

  let kindNote = "";
  if (r.solver === "failover") {
    kindNote = `
      <p>无解类别: <code>${esc(r.infeasible_kind || "")}</code>
      （${esc(KIND_TEXT[r.infeasible_kind] || "")}）</p>`;
  }

  // 仅缺少备援方案时, 展示最优常规方案逐阀失效的校验结果
  let bestBase = "";
  const bp = r.reason_detail && r.reason_detail.best_base_plan;
  if (bp) {
    bestBase = `
      <div class="card">
        <h3>最优常规方案的单阀失效校验（说明为何缺少备援）</h3>
        <p class="role-name">常规方案关闭: ${esc((bp.closed_valves || []).join(", ") || "(无)")}
        ，总代价 ${esc(bp.total_cost)}。下列任一计划阀门关不上都会导致方案失效。</p>
        ${bp.failure_checks && bp.failure_checks.length ? `
        <table><thead><tr><th>失效情形</th><th>实际关闭阀门</th><th>漏点</th><th>必保点</th><th>校验</th></tr></thead>
        <tbody>${scenarioCheckRows(bp.failure_checks)}</tbody></table>` : "<p>该常规方案不关闭任何阀门。</p>"}
      </div>`;
  }

  body.innerHTML = `
    <div class="card"><h3>无解原因</h3>
      <p>原因代码: <code>${esc(r.reason_code)}</code></p>
      ${kindNote}
      <p>${esc(r.message)}</p>
      ${r.reason_detail ? `<pre>${esc(JSON.stringify(r.reason_detail, null, 2))}</pre>` : ""}
    </div>${bestBase}`;
}

function renderFailover(r, banner, body) {
  banner.classList.add("ok");
  banner.innerHTML =
    `<span class="big">✓ 已求得单阀失效备援方案</span>${esc(r.message)}` +
    `<br><small>共枚举 ${r.subset_count} 个阀门组合: 常规可行 ${r.base_feasible_subset_count} 个, ` +
    `其中 ${r.failover_candidate_count} 个可承受任意单阀关不上。</small>`;

  const allChecks = [r.normal_check, ...r.failure_checks];
  body.innerHTML = `
    <div class="card">
      <h3>备援关阀清单（${r.closed_valves.length} 个, 总代价 ${r.total_cost}）</h3>
      ${valveListTable(r.closed_valves)}
      <p class="role-name">目标: 最小化计划关闭阀门的代价总和; 同代价时取编号升序表字典序最小者。</p>
    </div>

    <div class="card">
      <h3>逐阀失效校验（共 ${allChecks.length} 种情形: 正常执行 + ${r.failure_checks.length} 种单阀关不上）</h3>
      <table><thead><tr>
        <th>情形</th><th>实际关闭阀门</th><th>全部漏点与进水点断开</th>
        <th>全部必保点仍有供水</th><th>校验</th>
      </tr></thead>
      <tbody>${scenarioCheckRows(allChecks)}</tbody></table>
    </div>

    <div class="card">
      <h3>各节点供水状态（正常执行时）</h3>
      ${nodeStatusTable(r.node_status)}
    </div>

    <div class="card">
      <h3>各管段状态（正常执行时）</h3>
      ${segmentStatusTable(r.segment_status)}
    </div>`;
}

// ---------- 历史 ----------
async function loadHistory() {
  const data = await api("GET", "/api/plans?limit=20");
  $("#table-plans tbody").innerHTML = data.plans.length
    ? data.plans.map((p) => `
      <tr>
        <td>${p.id}</td><td>${esc(p.created_at.replace("+00:00", "Z"))}</td>
        <td><span class="tag ${p.solver === "failover" ? "flowing" : "dead"}">${esc(SOLVER_TEXT[p.solver] || p.solver || "常规")}</span></td>
        <td>${p.feasible ? '<span class="tag supplied">可行</span>' : '<span class="tag leak_not_isolated">无解</span>'}</td>
        <td>${p.total_cost ?? "—"}</td>
        <td>${p.closed_valves.map(esc).join(", ") || "—"}</td>
        <td><button data-view="${p.id}" class="secondary">查看</button></td>
      </tr>`).join("")
    : `<tr><td colspan="7" class="role-name">暂无历史方案</td></tr>`;
}

$("#table-plans").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  const plan = await api("GET", `/api/plans/${btn.dataset.view}`);
  const snap = plan.network_snapshot;
  state.nodes = snap.nodes.map((n) => ({ ...n }));
  state.segments = snap.segments.map((s) => ({ ...s }));
  state.valves = snap.valves.map((v) => ({ ...v }));
  renderAll();
  delete plan.network_snapshot;
  renderResult(plan);
  $("#section-result").scrollIntoView({ behavior: "smooth" });
});

// ---------- 顶部按钮 ----------
$("#btn-demo").addEventListener("click", async () => {
  const net = await api("POST", "/api/demo");
  applyNetwork(net.network);
  clearErrors();
});

$("#btn-clear").addEventListener("click", async () => {
  if (!confirm("确定清空全部节点、管段和阀门吗?")) return;
  await api("POST", "/api/clear");
  state.nodes = []; state.segments = []; state.valves = [];
  state.result = null;
  $("#result-body").innerHTML = "";
  $("#result-banner").classList.add("hidden");
  renderAll();
  loadHistory();
});

$("#btn-save").addEventListener("click", async () => {
  clearErrors();
  try {
    const res = await api("PUT", "/api/network", payloadFromUI());
    alert(`保存成功: ${res.counts.nodes} 个节点, ${res.counts.segments} 条管段, ${res.counts.valves} 个阀门`);
    loadHistory();
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

$("#btn-plan").addEventListener("click", async () => {
  clearErrors();
  try {
    const r = await api("POST", "/api/plan", payloadFromUI());
    renderResult(r);
    loadHistory();
    $("#section-result").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

$("#btn-plan-failover").addEventListener("click", async () => {
  clearErrors();
  try {
    const r = await api("POST", "/api/plan-failover", payloadFromUI());
    renderResult(r);
    loadHistory();
    $("#section-result").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

function applyNetwork(net) {
  state.nodes = (net.nodes || []).map((n) => ({ ...n, label: n.label || "" }));
  state.segments = (net.segments || []).map((s) => ({ ...s }));
  state.valves = (net.valves || []).map((v) => ({ ...v }));
  state.layout.clear();
  state.result = null;
  $("#result-body").innerHTML = "";
  $("#result-banner").classList.add("hidden");
  renderAll();
}

// ---------- Canvas 力导向示意 ----------
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");
const COLOR = {
  inlet: "#1c7fb3", leak: "#d13221", must_keep: "#2f9e44", normal: "#8a98a6",
};

function initLayout(w, h) {
  const cx = w / 2, cy = h / 2;
  state.nodes.forEach((n, i) => {
    if (!state.layout.has(n.id)) {
      const ang = (i / Math.max(1, state.nodes.length)) * Math.PI * 2;
      state.layout.set(n.id, {
        x: cx + Math.cos(ang) * Math.min(w, h) * 0.32,
        y: cy + Math.sin(ang) * Math.min(w, h) * 0.32,
      });
    }
  });
  // 删除已不存在的节点坐标
  for (const id of [...state.layout.keys()]) {
    if (!state.nodes.some((n) => n.id === id)) state.layout.delete(id);
  }
}

function simulation() {
  const pos = state.layout;
  const ids = state.nodes.map((n) => n.id);
  const idset = new Set(ids);
  const links = state.segments
    .filter((s) => idset.has(s.endpoint_a) && idset.has(s.endpoint_b))
    .map((s) => [s.endpoint_a, s.endpoint_b]);
  const W = canvas.width - 80, H = canvas.height - 80;
  for (let iter = 0; iter < 240; iter++) {
    const force = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
    // 斥力
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i]), b = pos.get(ids[j]);
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 1;
        const f = 9000 / d2;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        force.get(ids[i]).x += dx * f; force.get(ids[i]).y += dy * f;
        force.get(ids[j]).x -= dx * f; force.get(ids[j]).y -= dy * f;
      }
    }
    // 弹簧
    for (const [u, v] of links) {
      const a = pos.get(u), b = pos.get(v);
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 130) * 0.02;
      dx /= d; dy /= d;
      force.get(u).x += dx * f; force.get(u).y += dy * f;
      force.get(v).x -= dx * f; force.get(v).y -= dy * f;
    }
    for (const id of ids) {
      const p = pos.get(id), fr = force.get(id);
      p.x = Math.max(40, Math.min(W, p.x + fr.x));
      p.y = Math.max(40, Math.min(H, p.y + fr.y));
    }
  }
}

function drawCanvas() {
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!state.nodes.length) {
    ctx.fillStyle = "#8a98a6";
    ctx.font = "15px sans-serif";
    ctx.fillText("暂无管网数据 — 点击右上角「载入示例管网」快速体验", 220, h / 2);
    return;
  }
  initLayout(w, h);
  simulation();

  const closedSet = new Set((state.result && state.result.closed_valves) || []);
  const segStatus = new Map(
    ((state.result && state.result.segment_status) || []).map((s) => [s.id, s.status])
  );
  const nodeStatus = new Map(
    ((state.result && state.result.node_status) || []).map((n) => [n.id, n.status])
  );

  // 管段
  for (const s of state.segments) {
    const a = state.layout.get(s.endpoint_a);
    const b = state.layout.get(s.endpoint_b);
    if (!a || !b) continue;
    let color = "#5a6b7c", dash = [], width = 2;
    const st = segStatus.get(s.id);
    if (s.valve_id && closedSet.has(s.valve_id)) {
      color = "#d13221"; dash = [7, 5]; width = 3;
    } else if (st === "flowing") {
      color = "#1c7fb3"; width = 3;
    } else if (st === "dead") {
      color = "#b9c2cb";
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);

    // 阀门标记: 管段中点画小方块 + 编号
    if (s.valve_id) {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const closed = closedSet.has(s.valve_id);
      ctx.fillStyle = closed ? "#d13221" : "#fff";
      ctx.strokeStyle = "#444";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.rect(mx - 6, my - 6, 12, 12);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = closed ? "#d13221" : "#333";
      ctx.font = "10px sans-serif";
      ctx.fillText(s.valve_id, mx - 7, my - 10);
    }
  }

  // 节点
  for (const n of state.nodes) {
    const p = state.layout.get(n.id);
    let color = COLOR[n.role] || COLOR.normal;
    const st = nodeStatus.get(n.id);
    if (st === "disconnected") color = "#b9c2cb";
    ctx.beginPath();
    ctx.arc(p.x, p.y, 9, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#22303f";
    ctx.font = "bold 12px sans-serif";
    ctx.fillText(n.id, p.x + 11, p.y - 8);
    if (n.label && n.label !== n.id) {
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "#5a6b7c";
      ctx.fillText(n.label, p.x + 11, p.y + 5);
    }
  }
}

// ---------- 启动 ----------
(async function init() {
  try {
    const net = await api("GET", "/api/network");
    applyNetwork(net);
    await loadHistory();
  } catch (err) {
    showErrors([err.message]);
  }
  window.addEventListener("resize", drawCanvas);
})();
