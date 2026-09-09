// SUMO 交通红绿灯算法评价平台 - 前端逻辑

const state = {
  projectPath: '',
  projectInfo: null,
  currentStats: null,
  currentRunId: null,
  isRunning: false,
  savedResults: [],
  parameters: { total_vehicles: 500, duration: 600, seed: 42 }
};

// ─── API 客户端 ──────────────────────────────────────────────
async function api(url, opts = {}) {
  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...opts
    });
    return await res.json();
  } catch (e) {
    return { error: '无法连接到服务器，请确认后端已启动' };
  }
}

// ─── Toast ───────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `${type} show`;
  clearTimeout(el._timeout);
  el._timeout = setTimeout(() => { el.className = ''; }, 3000);
}

// ─── 状态栏 ──────────────────────────────────────────────────
function setStatus(status, msg) {
  const dot = document.querySelector('#status-bar .dot');
  const text = document.getElementById('status-text');
  dot.className = 'dot ' + status;
  text.textContent = msg;

  const btns = document.querySelectorAll('#sidebar .btn');
  btns.forEach(b => { b.disabled = status === 'running'; });
}

// ─── 项目操作 ────────────────────────────────────────────────
async function selectProject(path) {
  // 如果当前有结果且项目变了，提示保存
  if (state.currentStats && state.projectPath && path && state.projectPath !== path) {
    if (confirm('当前有未保存的仿真结果，是否先保存？')) {
      const label = prompt('请输入结果标签（可选）：', '');
      if (label !== null) {
        document.getElementById('save-label').value = label;
        await saveResults();
      }
    }
  }

  state.projectPath = path;
  state.currentStats = null;
  state.currentRunId = null;
  document.getElementById('project-select').value = path;
  document.getElementById('save-label').value = '';

  if (!path) {
    state.projectInfo = null;
    renderContent();
    return;
  }

  const data = await api('/api/projects/validate', {
    method: 'POST', body: JSON.stringify({ project_path: path })
  });

  if (data.valid) {
    state.projectInfo = data;
    renderProjectInfo();
    renderContent();  // 清空结果面板
    toast(`已加载项目: ${data.intersections.length} 个路口`, 'success');
  } else {
    state.projectInfo = null;
    toast(data.error || '项目加载失败', 'error');
    renderContent();
  }
}

async function scanProjects() {
  const data = await api('/api/projects/scan', {
    method: 'POST', body: JSON.stringify({})
  });
  if (data.projects) {
    populateProjectSelect(data.projects);
  }
}

function populateProjectSelect(projects) {
  const sel = document.getElementById('project-select');
  // Keep first option (手动输入)
  sel.innerHTML = '<option value="">-- 选择项目 --</option>';
  for (const p of projects) {
    const label = `${p.name} (${p.intersection_count}个路口)`;
    sel.innerHTML += `<option value="${p.path}">${label}</option>`;
  }
  // Restore selection
  if (state.projectPath)
    sel.value = state.projectPath;
}

function renderProjectInfo() {
  if (!state.projectInfo) return;
  const info = state.projectInfo;
  document.getElementById('proj-info').innerHTML = `
    <div style="font-size:13px;line-height:1.8;">
      <div><b>路网文件:</b> ${info.net_file}</div>
      <div><b>路口数量:</b> ${info.intersections.length}</div>
      <div><b>车流文件:</b> ${info.has_routes ? '已有' : '无'}</div>
      <div style="margin-top:8px;max-height:120px;overflow-y:auto;">
        ${info.intersections.map(i => `<span class="badge badge-green" style="margin:2px;">${i.id}: ${i.name}</span>`).join(' ')}
      </div>
    </div>
  `;
}

// ─── 参数变更 ────────────────────────────────────────────────
function updateParam(key, val) {
  state.parameters[key] = parseInt(val) || 0;
  document.getElementById(`val-${key}`).textContent = key === 'duration' ?
    `${state.parameters[key]}秒（${Math.floor(state.parameters[key]/60)}分${state.parameters[key]%60}秒）` :
    state.parameters[key];
}

// ─── 车流生成 ────────────────────────────────────────────────
async function generateRoutes() {
  if (!state.projectPath) { toast('请先选择项目', 'error'); return; }
  setStatus('running', '正在生成车流路由...');
  const data = await api('/api/routes/generate', {
    method: 'POST', body: JSON.stringify({
      project_path: state.projectPath,
      ...state.parameters
    })
  });
  setStatus('idle', data.success ? '车流生成完成' : '车流生成失败');
  toast(data.success ? data.message : (data.error || '生成失败'), data.success ? 'success' : 'error');
}

// ─── 运行仿真 ────────────────────────────────────────────────
async function runSimulation() {
  if (!state.projectPath) { toast('请先选择项目', 'error'); return; }
  if (state.isRunning) return;

  state.isRunning = true;
  setStatus('running', '正在运行仿真...');
  document.getElementById('progress-fill').style.width = '30%';

  const data = await api('/api/simulation/run', {
    method: 'POST', body: JSON.stringify({
      project_path: state.projectPath,
      ...state.parameters
    })
  });

  document.getElementById('progress-fill').style.width = '100%';
  state.isRunning = false;

  if (data.success) {
    state.currentStats = data.statistics;
    state.currentRunId = data.run_id;
    state._lastParams = { ...state.parameters };
    renderResults(data.statistics);
    setStatus('done', `仿真完成 — ${data.statistics.overall.total_vehicles} 辆车`);
    toast('仿真完成！', 'success');
  } else {
    setStatus('error', `仿真失败: ${data.error}`);
    toast(data.error || '仿真失败', 'error');
  }
  setTimeout(() => { document.getElementById('progress-fill').style.width = '0%'; }, 1500);
}

async function runSimulationGUI() {
  if (!state.projectPath) { toast('请先选择项目', 'error'); return; }
  setStatus('running', '正在启动 SUMO-GUI...');
  const data = await api('/api/simulation/run-gui', {
    method: 'POST', body: JSON.stringify({
      project_path: state.projectPath,
      ...state.parameters
    })
  });
  setStatus('idle', data.success ? 'SUMO-GUI 已启动' : '启动失败');
  toast(data.success ? data.message : (data.error || '启动失败'), data.success ? 'success' : 'error');
}

// ─── 保存结果 ────────────────────────────────────────────────
async function saveResults() {
  if (!state.currentStats) { toast('没有可保存的统计数据', 'error'); return; }
  const label = document.getElementById('save-label').value.trim();

  // 从项目路径提取项目名
  let projectName = '';
  if (state.projectInfo && state.projectInfo.intersections) {
    projectName = state.projectInfo.intersections.length > 1 ?
      state.projectPath.split('\\').pop().split('/').pop() :
      (state.projectInfo.intersections[0].name || 'unknown');
  } else {
    projectName = state.projectPath.split('\\').pop().split('/').pop();
  }

  const data = await api('/api/results/save', {
    method: 'POST', body: JSON.stringify({
      run_id: state.currentRunId,
      label: label,
      project_path: state.projectPath,
      project_name: projectName,
      parameters: state._lastParams || state.parameters,
      statistics: state.currentStats
    })
  });

  if (data.success) {
    toast(data.message, 'success');
    loadSavedResults();
  } else {
    toast(data.error || '保存失败', 'error');
  }
}

// ─── 加载历史结果 ────────────────────────────────────────────
async function loadSavedResults() {
  const data = await api('/api/results/list');
  if (data.results) {
    state.savedResults = data.results;
    renderSavedResults(data.results);
  }
}

async function viewSavedResult(runId) {
  const data = await api(`/api/results/${runId}`);
  if (data.statistics) {
    state.currentStats = data.statistics;
    state.currentRunId = data.run_id;
    state.projectPath = data.project_path;
    document.getElementById('project-select').value = data.project_path;
    renderResults(data.statistics);
    toast(`已加载历史结果: ${data.label || runId}`, 'info');
  }
}

async function deleteSavedResult(runId) {
  if (!confirm('确定删除此结果？')) return;
  const data = await api(`/api/results/${runId}`, { method: 'DELETE' });
  if (data.success) {
    toast(data.message, 'info');
    loadSavedResults();
  }
}

function renderSavedResults(results) {
  const container = document.getElementById('saved-results-body');
  if (!results.length) {
    container.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);padding:20px;">暂无保存结果</td></tr>';
    return;
  }
  container.innerHTML = results.map(r => `
    <tr class="saved-result-row">
      <td>${r.timestamp || ''}</td>
      <td>${r.label || '—'}</td>
      <td>${r.parameters.total_vehicles || '—'} 辆 / ${r.parameters.duration || '—'}秒</td>
      <td>${r.summary.avg_wait_time || '—'}s</td>
      <td>
        <button class="btn btn-outline" style="padding:4px 10px;font-size:11px;width:auto;margin:0 2px;" onclick="viewSavedResult('${r.run_id}')">查看</button>
        <button class="btn btn-outline" style="padding:4px 10px;font-size:11px;width:auto;margin:0 2px;color:var(--danger);" onclick="deleteSavedResult('${r.run_id}')">删除</button>
      </td>
    </tr>
  `).join('');
}

// ─── 评分公式说明 ────────────────────────────────────────────
function scoreFormulaHtml() {
  return `
  <div style="font-size:12px;line-height:1.9;color:var(--text-secondary);">
    <div style="margin-bottom:6px;font-weight:700;color:var(--text);">评分公式</div>
    <div><b>Score</b> = Efficiency<sup>0.5</sup> × Stability<sup>0.3</sup> × Fairness<sup>0.2</sup></div>
    <div>　效率 = 几何平均(完成率, 吞吐, LOS) × √(P95延误惩罚)</div>
    <div>　稳定 = 几何平均(跨周期得分, 溢出惩罚) × √(瓶颈惩罚)</div>
    <div>　公平 = (1 − 基尼系数) × (1 − 0.5 × 进口间最大差距)</div>
    <div style="margin-top:4px;color:#c0940c;">硬性筛选（一票否决）：LOS F（延误>80s）｜完成率<60%｜等待>4周期</div>
  </div>`;
}

function scorePanelHtml(score) {
  if (!score) return '';
  const passed = score.passed;
  let body = '';
  if (passed) {
    const eff = score.efficiency, stab = score.stability, fair = score.fairness;
    body = `
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:12px;">
      <div style="text-align:center;">
        <div style="font-size:32px;font-weight:700;color:${score.score > 0.7 ? 'var(--success)' : score.score > 0.4 ? 'var(--warning)' : 'var(--danger)'};">${score.score.toFixed(3)}</div>
        <div style="font-size:12px;color:var(--text-secondary);">综合评分</div>
      </div>
      <div style="flex:1;min-width:280px;">
        <div style="font-size:13px;margin-bottom:6px;"><b>分项得分</b></div>
        <div style="display:flex;gap:16px;flex-wrap:wrap;">
          <div>效率 <span style="font-weight:700;color:${eff.value > 0.6 ? 'var(--success)' : eff.value > 0.3 ? 'var(--warning)' : 'var(--danger)'};">${eff.value.toFixed(3)}</span></div>
          <div>稳定 <span style="font-weight:700;color:${stab.value > 0.6 ? 'var(--success)' : stab.value > 0.3 ? 'var(--warning)' : 'var(--danger)'};">${stab.value.toFixed(3)}</span></div>
          <div>公平 <span style="font-weight:700;color:${fair.value > 0.6 ? 'var(--success)' : fair.value > 0.3 ? 'var(--warning)' : 'var(--danger)'};">${fair.value.toFixed(3)}</span></div>
          ${eff.p95_delay ? `<div>P95延误 <span style="font-weight:700;">${eff.p95_delay}s</span></div>` : ''}
          ${stab.avg_wait_cycles ? `<div>平均等待 <span style="font-weight:700;">${stab.avg_wait_cycles}周期</span></div>` : ''}
        </div>
        <div style="margin-top:8px;border-top:1px dashed var(--border);padding-top:6px;">
          ${scoreFormulaHtml()}
        </div>
      </div>
    </div>`;
  } else {
    body = `
    <div style="color:var(--danger);font-size:13px;font-weight:600;">❌ 未通过硬性筛选，评分无效</div>
    <div style="font-size:12px;color:var(--text-secondary);margin-top:6px;">${(score.reasons||[]).join('；') || '原因未知'}</div>
    <div style="margin-top:8px;border-top:1px dashed var(--border);padding-top:6px;">
      ${scoreFormulaHtml()}
    </div>`;
  }
  return `
  <div class="panel">
    <div class="panel-header"><h3>📊 综合评分</h3><span class="toggle open">▼</span></div>
    <div class="panel-body">${body}</div>
  </div>`;
}

// ─── 渲染结果 ────────────────────────────────────────────────
function renderResults(stats) {
  const content = document.getElementById('content');
  const overall = stats.overall || {};
  const intersections = stats.intersections || [];

  let html = '';

  // 综合评分面板
  html += scorePanelHtml(stats.score);

  // 总体统计卡片
  html += '<div class="stats-grid">';
  html += statCard('总车辆数', overall.total_vehicles || 0, '辆');
  html += statCard('完成率', (overall.completion_rate || 0) + '%', '', 'good');
  html += statCard('平均每车等待', (overall.avg_wait_time || 0).toFixed(1), '秒', (overall.avg_wait_time || 0) > 60 ? 'bad' : 'warn');
  html += statCard('每车每路口等待', (overall.avg_wait_per_intersection || 0).toFixed(1), '秒', 'warn');
  html += statCard('总等待时间', (overall.total_wait_time || 0).toFixed(0), '秒', '');
  html += statCard('平均延误', (overall.avg_time_loss || 0).toFixed(1), '秒/车', 'warn');
  html += statCard('平均行驶时间', (overall.avg_duration || 0).toFixed(1), '秒', '');
  html += statCard('平均速度', (overall.avg_speed || 0).toFixed(1), 'km/h', '');
  html += '</div>';

  // 各路口对比图
  if (intersections.length > 0) {
    html += `
    <div class="panel">
      <div class="panel-header" onclick="togglePanel(this)">
        <h3>各路口平均等待时间对比</h3>
        <span class="toggle open">▼</span>
      </div>
      <div class="panel-body">
        <div class="chart-container">
          <canvas id="intersection-chart" height="300"></canvas>
        </div>
      </div>
    </div>`;
  }

  // 各路口详细表
  if (intersections.length > 0) {
    html += `
    <div class="panel">
      <div class="panel-header" onclick="togglePanel(this)">
        <h3>各路口详细数据</h3>
        <span class="toggle open">▼</span>
      </div>
      <div class="panel-body" style="overflow-x:auto;">
        <table id="intersection-table">
          <thead><tr>
            <th onclick="sortTable('intersection-table',0)">路口ID <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',1)">名称 <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',2)">车流量 <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',3)">总等待时间(s) <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',4)">平均等待时间(s) <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',5)">相位数量 <span class="sort-arrow">⇅</span></th>
            <th onclick="sortTable('intersection-table',6)">周期时长(s) <span class="sort-arrow">⇅</span></th>
            <th>操作</th>
          </tr></thead>
          <tbody>
            ${intersections.map((i, idx) => `
              <tr class="inter-row" onclick="toggleLaneDetail('inter-detail-${idx}')" style="cursor:pointer;">
                <td><b>${i.id}</b></td>
                <td>${i.name || i.id}</td>
                <td>${i.vehicle_count}</td>
                <td>${i.total_wait_time.toFixed(1)}</td>
                <td><span class="badge ${i.avg_wait_time > 60 ? 'badge-red' : i.avg_wait_time > 30 ? 'badge-yellow' : 'badge-green'}">${i.avg_wait_time.toFixed(1)}s</span></td>
                <td>${i.phase_count || '—'}</td>
                <td>${i.cycle_time || '—'}</td>
                <td>
                  <button class="btn btn-outline" style="padding:4px 8px;font-size:11px;width:auto;margin:0;" onclick="event.stopPropagation();openEditor('${i.id}')">✏️ 编辑交通灯</button>
                  <button class="btn btn-outline" style="padding:4px 8px;font-size:11px;width:auto;margin:0;color:var(--warning);" onclick="event.stopPropagation();openOptimizer('${i.id}')">⚙ 优化配时</button>
                </td>
              </tr>
              <tr class="inter-detail" id="inter-detail-${idx}" style="display:none;">
                <td colspan="8" style="padding:12px 20px;background:#fafbfc;">
                  <div style="font-weight:600;margin-bottom:8px;">各进口方向详细数据</div>
                  <table style="width:100%;font-size:12px;">
                    <thead><tr>
                      <th>边ID</th><th>进口方向</th><th>车流量</th><th>总等待时间(s)</th><th>平均速度(m/s)</th>
                    </tr></thead>
                    <tbody>
                      ${(i.edge_details || []).map(e => `
                        <tr>
                          <td>${e.edge_id}</td><td>${e.approach || '—'}</td>
                          <td>${e.vehicle_count}</td>
                          <td>${e.wait_time.toFixed(1)}</td>
                          <td>${e.avg_speed.toFixed(1)}</td>
                        </tr>
                      `).join('')}
                      ${(i.edge_details || []).length === 0 ? '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);">无详细车道数据</td></tr>' : ''}
                    </tbody>
                  </table>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
  }

  content.innerHTML = html;

  // 绘制柱状图
  if (intersections.length > 0) {
    setTimeout(() => drawIntersectionChart(intersections), 100);
  }
}

function statCard(label, value, unit, cls) {
  return `<div class="stat-card ${cls || ''}">
    <div class="stat-value">${value}<span style="font-size:14px;font-weight:400;"> ${unit}</span></div>
    <div class="stat-label">${label}</div>
  </div>`;
}

function toggleLaneDetail(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}

function openEditor(intersectionId) {
  if (!state.projectPath) return;
  var url = '/editor?project=' + encodeURIComponent(state.projectPath) + '&intersection=' + encodeURIComponent(intersectionId);
  window.open(url, '_blank');
}

function openOptimizer(intersectionId) {
  if (!state.projectPath) return;
  var url = '/optimizer?project=' + encodeURIComponent(state.projectPath) + '&intersection=' + encodeURIComponent(intersectionId);
  window.open(url, '_blank');
}

function renderContent() {
  document.getElementById('content').innerHTML = `
    <div class="empty-state">
      <div class="icon">🚦</div>
      <p>请选择一个项目开始评价</p>
      <p class="sub">选择项目后，配置参数并运行仿真</p>
    </div>`;
}

// ─── 柱状图 ──────────────────────────────────────────────────
function drawIntersectionChart(intersections) {
  const canvas = document.getElementById('intersection-chart');
  if (!canvas) return;

  const container = canvas.parentElement;
  canvas.width = container.clientWidth - 20;
  canvas.height = 300;

  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  canvas.width = (container.clientWidth - 20) * dpr;
  canvas.height = 300 * dpr;
  canvas.style.width = (container.clientWidth - 20) + 'px';
  canvas.style.height = '300px';
  ctx.scale(dpr, dpr);

  const w = container.clientWidth - 20;
  const h = 300;
  const pad = { top: 30, right: 20, bottom: 60, left: 60 };

  const maxVal = Math.max(...intersections.map(i => i.avg_wait_time), 1);
  const barW = Math.max(20, (w - pad.left - pad.right) / intersections.length * 0.7);
  const gap = (w - pad.left - pad.right) / intersections.length;

  // 背景
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, w, h);

  // Y轴网格线
  ctx.strokeStyle = '#ecf0f1';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (h - pad.top - pad.bottom) * i / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();

    // 标签
    const val = maxVal * (4 - i) / 4;
    ctx.fillStyle = '#7f8c8d';
    ctx.font = '11px "Segoe UI","PingFang SC",sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(val.toFixed(0) + 's', pad.left - 8, y + 4);
  }

  // 柱状图
  intersections.forEach((item, i) => {
    const x = pad.left + i * gap + (gap - barW) / 2;
    const barH = Math.max(4, (item.avg_wait_time / maxVal) * (h - pad.top - pad.bottom));
    const y = h - pad.bottom - barH;

    // 颜色
    let color = '#27ae60';
    if (item.avg_wait_time > 60) color = '#e74c3c';
    else if (item.avg_wait_time > 30) color = '#f39c12';

    // 渐变
    const grad = ctx.createLinearGradient(x, y, x, h - pad.bottom);
    grad.addColorStop(0, color);
    grad.addColorStop(1, color + '40');
    ctx.fillStyle = grad;
    ctx.beginPath();
    roundRect(ctx, x, y, barW, barH, 4);
    ctx.fill();

    // 数值标签
    ctx.fillStyle = '#2c3e50';
    ctx.font = '10px "Segoe UI","PingFang SC",sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(item.avg_wait_time.toFixed(1) + 's', x + barW / 2, y - 6);

    // X轴标签
    ctx.fillStyle = '#7f8c8d';
    ctx.font = '11px "Segoe UI","PingFang SC",sans-serif';
    ctx.save();
    ctx.translate(x + barW / 2, h - pad.bottom + 14);
    ctx.rotate(-0.5);
    ctx.fillText(item.id, 0, 0);
    ctx.restore();
  });
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ─── 面板折叠 ────────────────────────────────────────────────
function togglePanel(header) {
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.toggle');
  if (body.style.display === 'none') {
    body.style.display = '';
    toggle.classList.add('open');
  } else {
    body.style.display = 'none';
    toggle.classList.remove('open');
  }
}

// ─── 表格排序 ────────────────────────────────────────────────
function sortTable(tableId, colIdx) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  const numeric = colIdx >= 2;
  const isAsc = table.dataset.sortCol === String(colIdx) && table.dataset.sortDir === 'asc';
  table.dataset.sortCol = colIdx;
  table.dataset.sortDir = isAsc ? 'desc' : 'asc';

  rows.sort((a, b) => {
    let va = a.children[colIdx].textContent.trim();
    let vb = b.children[colIdx].textContent.trim();
    if (numeric) {
      va = parseFloat(va) || 0;
      vb = parseFloat(vb) || 0;
    }
    return isAsc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });

  rows.forEach(r => tbody.appendChild(r));
}

// ─── 打开文件夹 ──────────────────────────────────────────────
function openProjectFolder() {
  const input = document.createElement('input');
  input.type = 'file';
  input.webkitdirectory = true;
  input.onchange = async (e) => {
    const files = e.target.files;
    if (!files.length) return;

    // 从选中的文件中推断项目路径
    const firstPath = files[0].webkitRelativePath || files[0].name;
    // file.webkitRelativePath = "network/base_network.net.xml" 这类
    // 实际上 webkitdirectory 只给了文件相对路径，需要用户手动输入
    toast('请在下拉菜单中输入项目完整路径，或使用搜索框查找', 'info');
  };
  input.click();
}

// ─── 搜索项目路径 ────────────────────────────────────────────
async function searchProjectPath() {
  const val = document.getElementById('project-search').value.trim();
  if (!val) return;

  // 尝试直接验证
  const data = await api('/api/projects/validate', {
    method: 'POST', body: JSON.stringify({ project_path: val })
  });

  if (data.valid) {
    await selectProject(val);
    // 添加到下拉菜单
    const sel = document.getElementById('project-select');
    const exists = Array.from(sel.options).some(o => o.value === val);
    if (!exists) {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = `${val} (${data.intersections.length}个路口)`;
      sel.appendChild(opt);
      sel.value = val;
    }
  } else {
    toast(data.error || '无效的项目路径', 'error');
  }
}

// ─── 初始化 ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // 加载预置项目列表
  scanProjects();

  // 事件绑定
  document.getElementById('project-select').addEventListener('change', (e) => {
    if (e.target.value) selectProject(e.target.value);
  });

  document.getElementById('project-search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchProjectPath();
  });

  // 参数滑条
  ['total_vehicles', 'duration', 'seed'].forEach(key => {
    const el = document.getElementById(`param-${key}`);
    if (el) {
      el.addEventListener('input', () => updateParam(key, el.value));
    }
  });

  // 加载历史结果
  loadSavedResults();
});
