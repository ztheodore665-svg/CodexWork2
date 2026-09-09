<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import PanelCard from './ui/PanelCard.vue'
import { useMetricsStore } from '../stores/metrics'
import { useUiStore } from '../stores/ui'
import { useSimStore } from '../stores/sim'
import { apiGet } from '../api/http'

const metrics = useMetricsStore()
const ui = useUiStore()
const sim = useSimStore()

const chartEl = ref(null)
let chart = null

const CARDS = [
  { key: 'vehicle_count', label: '在网车辆', fmt: (v) => String(v), unit: '' },
  { key: 'avg_speed', label: '平均速度', fmt: (v) => (v * 3.6).toFixed(1), unit: 'km/h' },
  { key: 'avg_waiting_time', label: '平均等待', fmt: (v) => String(Math.round(v)), unit: 's' },
  { key: 'avg_queue_length', label: '平均排队', fmt: (v) => v.toFixed(1), unit: '辆' },
]

// ── 自定义指标（可在弹窗中增减启用项） ──────────────────────
const CUSTOM_DEFS = [
  { key: 'completion_rate', label: '完成率', type: 'value',
    fmt: (v) => v == null ? '暂无' : (v * 100).toFixed(0) + '%',
    desc: '已到达车辆 ÷ 已出发车辆，越高越好' },
  { key: 'total_departed', label: '累计出发', type: 'value',
    fmt: (v) => v == null ? '暂无' : String(v), desc: '仿真开始以来出发的车辆总数' },
  { key: 'total_arrived', label: '累计到达', type: 'value',
    fmt: (v) => v == null ? '暂无' : String(v), desc: '已到达目的地的车辆总数' },
  { key: 'longest_vehicle', label: '最久行驶车辆', type: 'locate', locate: 'vehicle',
    fmt: (d) => d ? `${d.id} · ${d.duration}s` : '暂无', desc: '在线最久的车辆，点击卡片定位' },
  { key: 'longest_wait_vehicle', label: '最长等待车辆', type: 'locate', locate: 'vehicle',
    fmt: (d) => d ? `${d.id} · 等${d.waiting_time}s` : '暂无', desc: '累计等待最久的车辆，点击卡片定位' },
  { key: 'most_congested_edge', label: '最堵塞道路', type: 'locate', locate: 'edge',
    fmt: (d) => d ? `${d.id} · ${d.queue}辆` : '暂无', desc: '排队车辆最多的道路，点击定位' },
  { key: 'most_congested_tls', label: '最堵塞路口', type: 'locate', locate: 'node',
    fmt: (d) => d ? `${d.id} · 排队${d.queue}` : '暂无', desc: '排队最多的路口，点击定位' },
]

// 显示顺序 = 用户启用的先后（默认评委顺序：最堵塞道路 → 最堵塞路口 → 完成率），
// 而非 CUSTOM_DEFS 定义顺序，保证"基础四卡 + 这些自定义卡"的阅读顺序可控
const enabledCustom = computed(() => {
  const byKey = new Map(CUSTOM_DEFS.map((d) => [d.key, d]))
  return (ui.settings.customMetrics || [])
    .map((k) => byKey.get(k))
    .filter(Boolean)
})

const spotlight = ref({})
let spotlightTimer = null
async function refreshSpotlight() {
  try { spotlight.value = await apiGet('/metrics/spotlight') } catch { /* 后端未起 */ }
}
watch([() => ui.settings.customMetrics, () => sim.status], () => {
  clearInterval(spotlightTimer)
  if (enabledCustom.value.length && sim.status !== 'idle') {
    refreshSpotlight()
    spotlightTimer = setInterval(refreshSpotlight, 3000)
  }
}, { immediate: true })

function onCustomClick(def) {
  if (def.type !== 'locate') return
  const d = spotlight.value[def.key]
  if (!d) return
  ui.setSpotlight({ type: def.locate, id: d.id })
}

// ── 自定义指标弹窗 ─────────────────────────────────────────
const pickerOpen = ref(false)
function toggleCustom(key) {
  const list = [...(ui.settings.customMetrics || [])]
  const i = list.indexOf(key)
  if (i >= 0) list.splice(i, 1)
  else list.push(key)
  ui.setSetting('customMetrics', list)
}

// ── 平均速度曲线（专业模式） ────────────────────────────────
function cssVar(n) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim()
}

function renderChart() {
  if (!chartEl.value) return
  chart = echarts.init(chartEl.value)
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 36, right: 10, top: 10, bottom: 24 },
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
      name: '平均速度', type: 'line', smooth: true, symbol: 'none',
      data: (metrics.history.series.avg_speed || []).map((v) => (v == null ? null : +(v * 3.6).toFixed(1))),
      lineStyle: { width: 1.5, color: cssVar('--signal-green') },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [
          { offset: 0, color: cssVar('--signal-green-soft') },
          { offset: 1, color: 'transparent' }] } },
    }],
  })
}

function resize() { chart?.resize() }

watch(() => ui.viewMode, (m) => {
  if (m === 'pro') { renderChart(); window.addEventListener('resize', resize) }
  else { chart?.dispose(); chart = null; window.removeEventListener('resize', resize) }
})

onMounted(() => {
  if (ui.viewMode === 'pro') { renderChart(); window.addEventListener('resize', resize) }
})
onBeforeUnmount(() => {
  clearInterval(spotlightTimer)
  chart?.dispose()
  window.removeEventListener('resize', resize)
})
</script>

<template>
  <PanelCard title="实时指标" class="metrics-panel">
    <div class="scroll-area">
      <div class="cards">
        <div v-for="c in CARDS" :key="c.key" class="card">
          <div class="label">{{ c.label }}</div>
          <div class="value mono">
            {{ c.fmt(metrics.overall[c.key] ?? 0) }}<span v-if="c.unit" class="unit">{{ c.unit }}</span>
            <span
              v-if="c.key === 'avg_speed'" class="trend"
              :class="metrics.speedTrend >= 0 ? 'up' : 'down'"
            >{{ metrics.speedTrend > 0 ? '↑' : metrics.speedTrend < 0 ? '↓' : '→' }}</span>
          </div>
        </div>

        <div v-for="c in enabledCustom" :key="c.key" class="card custom"
          :class="{ clickable: c.type === 'locate' }" :title="c.desc"
          @click="onCustomClick(c)">
          <div class="label">{{ c.label }}</div>
          <div class="value mono" :class="{ small: c.type === 'locate' }">{{ c.fmt(spotlight[c.key]) }}</div>
          <span v-if="c.type === 'locate'" class="locate-hint">点击定位 →</span>
        </div>
      </div>

      <div v-if="ui.viewMode === 'pro'" ref="chartEl" class="chart" />
    </div>

    <div class="custom-bar">
      <button class="custom-btn" @click="pickerOpen = true">
        自定义指标{{ enabledCustom.length ? `（${enabledCustom.length}）` : '' }}
      </button>
    </div>

    <!-- 自定义指标选择弹窗 -->
    <Teleport to="body">
      <div v-if="pickerOpen" class="picker-mask" @click.self="pickerOpen = false">
        <div class="picker-panel">
          <div class="picker-head">
            <span class="picker-title">自定义指标（{{ enabledCustom.length }}/{{ CUSTOM_DEFS.length }}）</span>
            <button class="picker-close" @click="pickerOpen = false">×</button>
          </div>
          <div class="picker-list">
            <div v-for="d in CUSTOM_DEFS" :key="d.key" class="pitem"
              :class="{ on: (ui.settings.customMetrics || []).includes(d.key) }"
              @click="toggleCustom(d.key)">
              <span class="pcheck">{{ (ui.settings.customMetrics || []).includes(d.key) ? '✓' : '' }}</span>
              <span class="plabel">{{ d.label }}</span>
              <span class="pdesc">{{ d.desc }}</span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </PanelCard>
</template>

<style scoped>
/* 面板内容区改为内部滚动：指标卡+曲线可滚动，自定义指标按钮固定沉底 */
.metrics-panel :deep(.panel-body) {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.scroll-area { flex: 1; min-height: 0; overflow: auto; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.card {
  padding: var(--space-3);
  background: var(--bg-elev); border: 1px solid var(--border);
  border-radius: var(--radius-ctrl);
  position: relative;
}
.card.custom.clickable { cursor: pointer; }
.card.custom.clickable:hover { border-color: var(--accent); }
.label { font-size: 11px; color: var(--text-3); margin-bottom: 4px; }
.value { font-size: 24px; font-weight: 700; letter-spacing: 0.01em; }
.value.small { font-size: 13px; line-height: 1.5; word-break: break-all; padding-bottom: 12px; }
.unit { font-size: 11px; color: var(--text-3); margin-left: 3px; font-weight: 400; }
.trend { font-size: 13px; margin-left: 4px; }
.trend.up { color: var(--signal-green); }
.trend.down { color: var(--signal-red); }
.locate-hint { position: absolute; bottom: 4px; right: 8px; font-size: 9px; color: var(--text-3); }
.chart { height: 140px; margin-top: var(--space-3); }
.custom-bar { flex: 0 0 auto; margin-top: var(--space-2); display: flex; justify-content: flex-end; }
.custom-btn {
  height: 22px; padding: 0 10px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-ctrl);
  background: var(--bg-elev); color: var(--text-2); font-size: 11px; cursor: pointer;
}
.custom-btn:hover { border-color: var(--accent); color: var(--accent); }

/* 选择弹窗 */
.picker-mask {
  position: fixed; inset: 0; z-index: 160;
  background: rgb(0 0 0 / 0.45);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.picker-panel {
  width: min(440px, 92vw); max-height: 80vh; overflow: auto;
  background: var(--bg-panel); border: 1px solid var(--border-strong);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-2);
  padding: var(--space-4);
}
.picker-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3); }
.picker-title { font-size: 15px; font-weight: 700; }
.picker-close { border: none; background: none; color: var(--text-3); font-size: 20px; cursor: pointer; }
.picker-close:hover { color: var(--signal-red); }
.picker-list { display: flex; flex-direction: column; gap: 4px; }
.pitem {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-ctrl);
  cursor: pointer; background: var(--bg-elev);
}
.pitem:hover { border-color: var(--accent); }
.pitem.on { border-color: var(--accent); background: var(--accent-soft); }
.pcheck {
  width: 18px; height: 18px; flex: 0 0 auto; border-radius: 4px;
  border: 1px solid var(--border-strong); display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: oklch(0.16 0.01 80); background: var(--bg-panel);
}
.pitem.on .pcheck { background: var(--accent); border-color: var(--accent); }
.plabel { font-size: 12px; color: var(--text-1); flex: 0 0 auto; }
.pdesc { font-size: 10px; color: var(--text-3); }
</style>
