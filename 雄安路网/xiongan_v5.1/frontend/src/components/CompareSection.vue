<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import PanelCard from './ui/PanelCard.vue'
import { useMetricsStore } from '../stores/metrics'
import { useUiStore } from '../stores/ui'
import { useSimStore } from '../stores/sim'
import { useEventStore } from '../stores/events'
import { apiGet } from '../api/http'

const metrics = useMetricsStore()
const ui = useUiStore()
const sim = useSimStore()
const events = useEventStore()

// 高度由父组件控制（多根组件不能透传 :style，需显式 prop）
defineProps({ height: { type: Number, default: 200 } })

const liveEl = ref(null)
const evalEl = ref(null)
let liveChart = null
let evalChart = null
let scoreTimer = null
const score = ref(null)

// ── 全屏数据表格 ──────────────────────────────────────────
const fullscreen = ref(false)
const collapsed = ref(false)   // 底部栏折叠为标题条
const rt = ref({ overall: {}, intersections: {} })
const CYCLES = 90  // 周期假设（与后端实时评分一致）
let fsTimer = null

// ── 评估报告生成 ───────────────────────────────────────────
const reportOpen = ref(false)
const generating = ref(false)
const reportErr = ref('')
const REPORT_SECTIONS = [
  { key: 'overview', label: '全局概况（实时指标）' },
  { key: 'score', label: '综合评分' },
  { key: 'history', label: '区间历史统计' },
  { key: 'intersections', label: '各路口评估' },
  { key: 'charts', label: '图表（matplotlib 绘制）' },
  { key: 'events', label: '事件日志' },
]
const reportCfg = ref({
  format: 'md',
  start: 0,
  end: 0,            // 0 = 截至当前仿真时刻
  sections: REPORT_SECTIONS.map((s) => s.key),
})
function openReport() {
  reportErr.value = ''
  reportCfg.value.end = sim.simTime || 0
  reportOpen.value = true
}
async function downloadReport() {
  const { format, start, end, sections } = reportCfg.value
  if (end && end <= start) { reportErr.value = '结束时间必须大于开始时间'; return }
  generating.value = true
  reportErr.value = ''
  try {
    const res = await fetch('/api/v1/report/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format, start, end, sections }),
    })
    if (!res.ok) {
      const j = await res.json().catch(() => null)
      throw new Error((j && j.message) || `HTTP ${res.status}`)
    }
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/)
    const name = m ? decodeURIComponent(m[1]) : `评估报告.${format}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
    reportOpen.value = false
  } catch (e) {
    reportErr.value = e.message
  } finally {
    generating.value = false
  }
}

function losLevel(d) {
  if (d <= 10) return 'A'
  if (d <= 20) return 'B'
  if (d <= 35) return 'C'
  if (d <= 55) return 'D'
  if (d <= 80) return 'E'
  return 'F'
}
function losScore(d) {
  if (d <= 10) return 1.0
  if (d <= 20) return 0.85
  if (d <= 35) return 0.7
  if (d <= 55) return 0.5
  if (d <= 80) return 0.3
  return Math.max(0.05, 0.3 * (100 - d) / 20)
}
async function refreshFullscreen() {
  try {
    const m = await apiGet('/metrics/realtime')
    rt.value = m
  } catch { /* 后端未起 */ }
}
function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  if (fullscreen.value) {
    refreshFullscreen()
    fsTimer = setInterval(refreshFullscreen, 3000)
  } else {
    clearInterval(fsTimer)
  }
}
const tlsRows = computed(() => Object.entries(rt.value.intersections || {})
  .map(([tid, v]) => {
    const wait = v.waiting_time || 0
    const cycleRatio = wait / CYCLES
    return {
      id: tid, queue: v.queue_length || 0, wait,
      los: losLevel(wait), score: losScore(wait),
      filter: wait > 80 || cycleRatio > 4,
    }
  }))
const overall = computed(() => rt.value.overall || {})

// 综合评分：老评价平台分层聚合公式（GET /evaluate/score，实时指标近似）
async function fetchScore() {
  try { score.value = await apiGet('/evaluate/score') } catch { /* 后端未起 */ }
}

// 离线评估数据：来自《实验评估报告》§3.1（routes_clean700，贪心推理窗口均值）
const EVAL = [
  { name: '固定配时', veh: 38.5, speed: 3.07, wait: 25, color: 'oklch(0.6 0.02 80)' },
  { name: 'SCOOT', veh: 63.5, speed: 1.71, wait: 60, color: 'oklch(0.80 0.13 75)' },
  { name: 'MAPPO', veh: 49.0, speed: 1.77, wait: 45, color: 'oklch(0.78 0.15 150)' },
]

function cssVar(n) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim()
}

// ── 实时曲线（本会话 /metrics/history 真实数据） ─────────────
function renderLive() {
  if (!liveEl.value) return
  liveChart = echarts.init(liveEl.value)
  liveChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 36, right: 10, top: 26, bottom: 22 },
    legend: { textStyle: { color: cssVar('--text-2'), fontSize: 10 }, top: 0 },
    xAxis: {
      type: 'category', data: metrics.history.step || [],
      axisLine: { lineStyle: { color: cssVar('--border-strong') } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 9 },
    },
    yAxis: {
      type: 'value', name: 'km/h',
      nameTextStyle: { color: cssVar('--text-3'), fontSize: 9 },
      splitLine: { lineStyle: { color: cssVar('--border'), type: 'dashed' } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 9 },
    },
    series: [{
      name: '平均速度（本会话）', type: 'line', smooth: true, symbol: 'none',
      data: (metrics.history.series.avg_speed || []).map((v) => (v == null ? null : +(v * 3.6).toFixed(1))),
      lineStyle: { width: 2, color: cssVar('--signal-green') },
      itemStyle: { color: cssVar('--signal-green') },
    }],
  })
}

// ── 离线评估对比（真实数据，等待时间越低越好） ───────────────
function renderEval() {
  if (!evalEl.value) return
  evalChart = echarts.init(evalEl.value)
  evalChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 36, right: 10, top: 12, bottom: 22 },
    xAxis: {
      type: 'category', data: EVAL.map((e) => e.name),
      axisLine: { lineStyle: { color: cssVar('--border-strong') } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 10 },
    },
    yAxis: {
      type: 'value', name: '秒', nameTextStyle: { color: cssVar('--text-3'), fontSize: 9 },
      splitLine: { lineStyle: { color: cssVar('--border'), type: 'dashed' } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 9 },
    },
    series: [{
      name: '平均等待时间', type: 'bar', barWidth: 28,
      data: EVAL.map((e) => ({ value: e.wait, itemStyle: { color: e.color, borderRadius: [3, 3, 0, 0] } })),
      label: { show: true, position: 'top', color: cssVar('--text-2'), fontSize: 10, fontFamily: 'monospace' },
    }],
  })
}

function resize() { liveChart?.resize(); evalChart?.resize() }

// 历史数据刷新 → 更新实时曲线
watch(() => metrics.history.step.length, () => {
  if (liveChart) liveChart.setOption({
    xAxis: { data: metrics.history.step },
    series: [{ data: (metrics.history.series.avg_speed || []).map((v) => (v == null ? null : +(v * 3.6).toFixed(1))) }],
  })
})
// 主题切换 → 重建图表（取色不同）
watch(() => ui.theme, () => {
  liveChart?.dispose(); liveChart = null
  evalChart?.dispose(); evalChart = null
  renderLive(); renderEval()
})

onMounted(() => {
  renderLive(); renderEval()
  window.addEventListener('resize', resize)
  fetchScore()
  scoreTimer = setInterval(() => { if (sim.status !== 'idle') fetchScore() }, 10000)
})
// 折叠/展开后重建图表（v-if 卸载重建了 DOM）
watch(collapsed, (v) => {
  if (!v) setTimeout(() => { renderLive(); renderEval() }, 0)
  else { liveChart?.dispose(); liveChart = null; evalChart?.dispose(); evalChart = null }
})
// 离线评估开关：关闭时销毁柱状图，重新打开时重建
watch(() => ui.settings.showEvalCharts, (v) => {
  if (v) setTimeout(() => { if (!evalChart) renderEval() }, 0)
  else { evalChart?.dispose(); evalChart = null }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  clearInterval(scoreTimer)
  clearInterval(fsTimer)
  liveChart?.dispose(); evalChart?.dispose()
})
</script>

<template>
  <div class="compare-root" :style="{ height: height + 'px' }">
  <PanelCard class="compare-card" title="方案对比">
    <template #extra>
      <span class="mono source">数据源：本会话 + 评估报告</span>
      <button class="fs-btn" @click="collapsed = !collapsed" :title="collapsed ? '展开' : '折叠'">
        {{ collapsed ? '▲ 展开' : '▼ 折叠' }}
      </button>
      <button class="fs-btn" @click="toggleFullscreen">⛶ 全屏数据</button>
      <button class="fs-btn" @click="openReport" title="按指定模拟时间区间生成评估报告（md/docx/pdf）">生成报告</button>
    </template>

    <template v-if="!collapsed">
    <div class="sec-label">实时曲线 · 平均速度（本仿真会话）</div>
    <div ref="liveEl" class="chart" />

    <template v-if="ui.settings.showEvalCharts">
      <div class="sec-label">离线评估 · routes_clean700（等待越低越好）</div>
      <div ref="evalEl" class="chart" />

      <table class="eval-table mono">
        <thead><tr><th>方案</th><th>在网车辆 ↑</th><th>平均速度 ↑ km/h</th><th>平均等待 ↓ s</th></tr></thead>
        <tbody>
          <tr v-for="e in EVAL" :key="e.name">
            <td><span class="swatch" :style="{ background: e.color }" />{{ e.name }}</td>
            <td>{{ e.veh }}</td>
            <td>{{ (e.speed * 3.6).toFixed(1) }}</td>
            <td>{{ e.wait }}</td>
          </tr>
        </tbody>
      </table>
      <p class="note">
        ↑ 越大越好，↓ 越小越好。参考值：平均速度 ≥20 km/h 为畅通线（限速 50 km/h 的 40% 归一化线）；
        平均等待 ≤35 s 为 LOS C 及格、≤20 s 良好（HCM 控制延误分级）；
        在网车辆体现承载/吞吐能力，越高越好（实测样本 38~64 veh）。
      </p>
    </template>

    <div class="sec-label">综合评分 · 老评价平台分层聚合公式（实时近似）</div>
    <div v-if="score" class="score-card">
      <template v-if="score.passed">
        <div class="score-main">
          <span class="score-num mono">{{ score.score.toFixed(3) }}</span>
          <span class="score-caption">满分 1.0 · 越大越好（效率^0.5 × 稳定^0.3 × 公平^0.2）</span>
        </div>
        <div class="score-bars">
          <div v-for="k in ['efficiency', 'stability', 'fairness']" :key="k" class="bar-row">
            <span class="bar-lbl mono" :title="k === 'efficiency' ? '完成率×吞吐×LOS，满分1.0' : k === 'stability' ? '跨周期×溢出×瓶颈，满分1.0' : '公平性，满分1.0'">{{ k }}</span>
            <div class="bar-track"><div class="bar-fill" :style="{ width: (score[k].value * 100) + '%' }" /></div>
            <span class="bar-val mono">{{ score[k].value.toFixed(3) }}</span>
          </div>
        </div>
        <p class="score-note">三项子分均满分 1.0、越大越好；参考：≥0.85 优秀、≥0.7 良好、&lt;0.5 待改进（经验参考）。实时近似；精确数据见 ⛶ 全屏数据</p>
      </template>
      <div v-else class="score-fail mono">
        硬性筛选未通过：{{ score.reasons.join('；') }}
      </div>
    </div>
    <div v-else class="score-card score-fail mono">评分未就绪（需仿真运行中）</div>
    </template>
  </PanelCard>

  <!-- 全屏数据表格 -->
  <Teleport to="body">
    <div v-if="fullscreen" class="fs-mask" @click.self="toggleFullscreen">
      <div class="fs-panel">
        <div class="fs-head">
          <span class="fs-title">完整数据面板（每 3 秒刷新）</span>
          <button class="fs-close" @click="toggleFullscreen">×</button>
        </div>

        <div class="fs-grid">
          <section class="fs-sec">
            <h4>全局指标</h4>
            <table class="fs-table mono">
              <tbody>
                <tr><td>在网车辆</td><td>{{ overall.vehicle_count ?? 0 }}</td></tr>
                <tr><td>平均速度</td><td>{{ ((overall.avg_speed ?? 0) * 3.6).toFixed(1) }} km/h</td></tr>
                <tr><td>平均等待</td><td>{{ Math.round(overall.avg_waiting_time ?? 0) }} s</td></tr>
                <tr><td>平均排队（辆/边）</td><td>{{ (overall.avg_queue_length ?? 0).toFixed(2) }}</td></tr>
                <tr><td>累计到达</td><td>{{ overall.total_throughput ?? 0 }}</td></tr>
                <tr><td>完成率</td>
                  <td>
                    {{ (((overall.total_throughput ?? 0) / Math.max(1, (overall.total_throughput ?? 0) + (overall.vehicle_count ?? 0))) * 100).toFixed(1) }}%
                  </td></tr>
                <tr><td>综合评分</td><td v-if="score">{{ score.passed ? score.score : '未通过筛选' }}</td></tr>
              </tbody>
            </table>
          </section>

          <section class="fs-sec">
            <h4>各路口评分（evaluator LOS 公式）</h4>
            <table class="fs-table mono">
              <thead><tr><th>路口</th><th>排队</th><th>等待 s</th><th>LOS</th><th>评分</th><th>筛选</th></tr></thead>
              <tbody>
                <tr v-for="r in tlsRows" :key="r.id" :class="{ bad: r.filter }">
                  <td>{{ r.id }}</td><td>{{ r.queue }}</td><td>{{ Math.round(r.wait) }}</td>
                  <td>{{ r.los }}</td><td>{{ r.score.toFixed(2) }}</td>
                  <td>{{ r.filter ? '⚠ 淘汰' : '✓' }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="fs-sec">
            <h4>历史序列（最近 20 步）</h4>
            <table class="fs-table mono">
              <thead><tr><th>步</th><th>速度 km/h</th><th>延误 s</th><th>排队</th></tr></thead>
              <tbody>
                <tr v-for="(s, i) in metrics.history.step.slice(-20)" :key="i">
                  <td>{{ s }}</td>
                  <td>{{ ((metrics.history.series.avg_speed?.[metrics.history.step.length - 20 + i] ?? 0) * 3.6).toFixed(1) }}</td>
                  <td>—</td><td>—</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="fs-sec">
            <h4>事件日志</h4>
            <table class="fs-table mono">
              <thead><tr><th>类型</th><th>步</th><th>受影响边</th></tr></thead>
              <tbody>
                <tr v-for="(e, i) in events.list.slice(-15).reverse()" :key="i">
                  <td>{{ e.event_type }}</td><td>{{ e.step }}</td>
                  <td>{{ (e.params?.edge_ids || []).join(', ') || '全入口' }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 评估报告配置窗 -->
  <Teleport to="body">
    <div v-if="reportOpen" class="fs-mask" @click.self="reportOpen = false">
      <div class="fs-panel rp-panel">
        <div class="fs-head">
          <span class="fs-title">生成评估报告</span>
          <button class="fs-close" @click="reportOpen = false">×</button>
        </div>

        <div class="rp-sec">
          <div class="rp-label">报告格式</div>
          <div class="seg">
            <button v-for="f in ['md', 'docx', 'pdf']" :key="f" class="seg-btn"
              :class="{ on: reportCfg.format === f }"
              @click="reportCfg.format = f">{{ f.toUpperCase() }}</button>
          </div>
          <div class="rp-hint">md 为 Markdown（图片内嵌，浏览器直接预览）；docx 为 Word；pdf 为 PDF。</div>
        </div>

        <div class="rp-sec">
          <div class="rp-label">评估时间区间（当前路网模拟时间，秒）</div>
          <div class="rp-range">
            <span class="rp-range-label">开始</span>
            <input v-model.number="reportCfg.start" type="number" min="0" class="rp-input mono" />
            <span class="rp-range-label">结束</span>
            <input v-model.number="reportCfg.end" type="number" min="0" class="rp-input mono" />
          </div>
          <div class="rp-hint">结束填 0 表示截至当前仿真时刻（当前 {{ sim.simTime || 0 }} s）。区间只统计该段时间内的历史数据。</div>
        </div>

        <div class="rp-sec">
          <div class="rp-label">内容组成</div>
          <div class="rp-sections">
            <label v-for="s in REPORT_SECTIONS" :key="s.key" class="rp-check">
              <input type="checkbox" :value="s.key" v-model="reportCfg.sections" />
              <span>{{ s.label }}</span>
            </label>
          </div>
        </div>

        <div v-if="reportErr" class="rp-err mono">{{ reportErr }}</div>

        <div class="rp-actions">
          <button class="fs-btn" @click="reportOpen = false">取消</button>
          <button class="rp-gen" :disabled="generating" @click="downloadReport">
            {{ generating ? '生成中…' : '生成并下载' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
  </div>
</template>

<style scoped>
.compare-root { display: flex; flex-direction: column; min-height: 0; }
.compare-card { flex: 1; min-height: 0; }
.source { font-size: 10px; color: var(--text-3); }
.sec-label { font-size: 11px; color: var(--text-2); margin: var(--space-3) 0 var(--space-2); }
.chart { height: 130px; }
.eval-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: var(--space-2); }
.eval-table th, .eval-table td { padding: 5px 8px; text-align: right; border-bottom: 1px solid var(--border); }
.eval-table th { color: var(--text-3); font-weight: 500; font-size: 11px; }
.eval-table td:first-child { text-align: left; color: var(--text-1); }
.swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 6px; }
.note { font-size: 11px; color: var(--text-3); line-height: 1.7; margin-top: var(--space-2); }
.score-card {
  margin-top: var(--space-2); padding: var(--space-3);
  border: 1px solid var(--border); border-radius: var(--radius-panel);
  background: var(--bg-elev);
}
.score-main { display: flex; align-items: baseline; gap: var(--space-3); margin-bottom: var(--space-2); }
.score-num { font-size: 34px; font-weight: 700; color: var(--signal-green); }
.score-caption { font-size: 11px; color: var(--text-3); }
.score-bars { display: flex; flex-direction: column; gap: 4px; }
.bar-row { display: flex; align-items: center; gap: 8px; }
.bar-lbl { width: 70px; font-size: 11px; color: var(--text-2); }
.bar-track { flex: 1; height: 6px; border-radius: 3px; background: var(--bg-active); overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 3px; }
.bar-val { width: 46px; text-align: right; font-size: 11px; color: var(--text-2); }
.score-fail { color: var(--signal-red); font-size: 11px; padding: 6px 0; }
.score-note { font-size: 10px; color: var(--text-3); margin-top: var(--space-2); }
.fs-btn {
  height: 22px; padding: 0 8px; border: 1px solid var(--border-strong); border-radius: var(--radius-ctrl);
  background: var(--bg-elev); color: var(--text-1); font-size: 11px; cursor: pointer; white-space: nowrap;
}
.fs-btn:hover { border-color: var(--accent); color: var(--accent); }
.fs-mask {
  position: fixed; inset: 0; z-index: 200; background: rgb(0 0 0 / 0.55);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.fs-panel {
  width: min(1200px, 94vw); max-height: 92vh; overflow: auto;
  background: var(--bg-panel); border: 1px solid var(--border-strong);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-2); padding: var(--space-4);
}
.fs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3); }
.fs-title { font-size: 15px; font-weight: 700; }
.fs-close { border: none; background: none; color: var(--text-3); font-size: 20px; cursor: pointer; }
.fs-close:hover { color: var(--signal-red); }
.fs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
.fs-sec h4 { font-size: 12px; color: var(--text-2); margin: 0 0 var(--space-2); }
.fs-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.fs-table th, .fs-table td { padding: 4px 8px; text-align: right; border-bottom: 1px solid var(--border); }
.fs-table th { color: var(--text-3); font-weight: 500; font-size: 11px; }
.fs-table td:first-child { text-align: left; }
.fs-table tr.bad td { color: var(--signal-red); }

/* 报告配置窗 */
.rp-panel { width: min(520px, 94vw); }
.rp-sec { margin-bottom: var(--space-4); }
.rp-label { font-size: 12px; color: var(--text-2); margin-bottom: 6px; }
.rp-hint { font-size: 10px; color: var(--text-3); margin-top: 4px; line-height: 1.6; }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-ctrl); overflow: hidden; }
.seg-btn {
  height: 26px; padding: 0 16px; border: none; background: var(--bg-elev);
  color: var(--text-2); font-size: 12px; cursor: pointer; white-space: nowrap;
}
.seg-btn + .seg-btn { border-left: 1px solid var(--border-strong); }
.seg-btn.on { background: var(--accent); color: oklch(0.16 0.01 80); }
.rp-range { display: flex; align-items: center; gap: 8px; }
.rp-range-label { font-size: 12px; color: var(--text-2); }
.rp-input {
  width: 110px; height: 26px; padding: 0 8px;
  background: var(--bg-elev); color: var(--text-1);
  border: 1px solid var(--border); border-radius: var(--radius-ctrl); font-size: 12px;
}
.rp-sections { display: flex; flex-direction: column; gap: 6px; }
.rp-check {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--text-1); cursor: pointer;
}
.rp-check input { accent-color: var(--accent); }
.rp-err { font-size: 11px; color: var(--signal-red); margin-bottom: var(--space-3); }
.rp-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
.rp-gen {
  height: 26px; padding: 0 16px;
  border: 1px solid var(--accent); border-radius: var(--radius-ctrl);
  background: var(--accent); color: oklch(0.16 0.01 80); font-size: 12px; cursor: pointer;
}
.rp-gen:disabled { opacity: 0.5; cursor: wait; }
</style>
