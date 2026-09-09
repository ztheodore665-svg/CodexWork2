<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TopBar from './components/TopBar.vue'
import NetCanvas from './components/NetCanvas.vue'
import AgentPanel from './components/AgentPanel.vue'
import MetricsPanel from './components/MetricsPanel.vue'
import SchemePanel from './components/SchemePanel.vue'
import EventPanel from './components/EventPanel.vue'
import CompareSection from './components/CompareSection.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { useUiStore } from './stores/ui'
import { useSimStore } from './stores/sim'
import { useAgentStore } from './stores/agent'
import { useMetricsStore } from './stores/metrics'
import { initRealtime, subscribe } from './realtime'

const ui = useUiStore()
const sim = useSimStore()
const agent = useAgentStore()
const metrics = useMetricsStore()

const settingsOpen = ref(false)

// ── 响应式断点：窄屏把侧栏从"固定占宽"切换为"悬浮抽屉"，给画布让出整屏 ──
const compact = ref(false)   // ≤1100px：右栏（AI 助手）变抽屉
const narrow = ref(false)    // ≤820px：左栏也变抽屉
const rightOpen = ref(true)
const leftOpen = ref(true)
let resizeRaf = null
function updateBp() {
  const W = window.innerWidth
  const wasCompact = compact.value
  const wasNarrow = narrow.value
  compact.value = W <= 1100
  narrow.value = W <= 820
  // 刚跨入断点时默认收起对应抽屉，画布立即获得整屏
  if (!wasCompact && compact.value) rightOpen.value = false
  if (!wasNarrow && narrow.value) leftOpen.value = false
  ui.reclampLayout()
}
function onResize() {
  if (resizeRaf) return
  resizeRaf = requestAnimationFrame(() => { resizeRaf = null; updateBp() })
}
const leftStyle = computed(() => narrow.value ? { width: 'min(320px, 86vw)' } : { width: ui.leftW + 'px' })
const rightStyle = computed(() => compact.value ? { width: 'min(360px, 88vw)' } : { width: ui.rightW + 'px' })

// ── 左栏单栏切换模式（tabs）：一次只显示一个子栏 ────────────
const LEFT_TABS = [
  { key: 'metrics', label: '指标' },
  { key: 'scheme', label: '方案' },
  { key: 'event', label: '事件' },
]
const leftTabs = computed(() => ui.viewMode === 'pro'
  ? LEFT_TABS
  : LEFT_TABS.filter((t) => t.key === 'metrics'))
const leftTab = computed(() => {
  const t = ui.settings.leftTab
  return leftTabs.value.some((x) => x.key === t) ? t : 'metrics'
})

let timers = []
let drag = null
let autoStarted = false   // 页面加载自动启动默认演示（仅一次）

/** 评委开箱即用：自动以默认配置启动 base_network(20路口) + 平峰车流 + 官方方案(mappo优化)，5× 速度 */
async function autoStartDefault() {
  if (autoStarted || sim.status !== 'idle') return
  autoStarted = true
  const b = sim.nets.find((n) => n.name === 'base_network')
  if (!b) return
  const routes = b.routes || []
  const match = routes.find((p) => /clean700|traffic_med/.test(p)) || routes[0] || ''
  const adds = (b.adds || []).find((p) => /timing_safe/.test(p)) || ''
  try {
    await sim.start({
      netPath: b.net_path,
      routes: match ? [match] : [],
      addFiles: adds ? [adds] : [],
      scheme: 'official',          // 默认官方方案（mappo优化）
      schemeParams: { mode: 'auto', decision_step: 60 },
      scenario: 'normal',          // 默认平峰车流（渐入需手动选择）
    })
    sim.setSpeed(5).catch(() => {})
  } catch (e) {
    console.warn('[auto-start] 失败（可手动启动）:', e.message)
    autoStarted = false
  }
}

onMounted(async () => {
  ui.apply()
  initRealtime()

  subscribe('metrics_update', (d) => metrics.applyWs(d))
  subscribe('simulation_step', (d) => { sim.applyStep(d); metrics.applyStep(d) })

  agent.fetchStatus()
  metrics.refresh()

  // 新打开页面：若后端残留上一会话（上次关页面前没停止），自动清空，
  // 保证打开即是全新空白，不默认回到上次的路网
  await sim.refreshStatus()
  if (sim.status !== 'idle') {
    try { await sim.stop(); metrics.resetSession() } catch { sim.status = 'idle'; sim.sessionId = null; metrics.resetSession() }
  }

  await sim.listNetworks()          // 等路网清单就绪
  setTimeout(() => autoStartDefault(), 800)   // 稍等预览渲染后自动开跑默认演示

  updateBp()
  window.addEventListener('resize', onResize)
  timers.push(setInterval(() => sim.refreshStatus(), 3000))
  timers.push(setInterval(() => { if (sim.status !== 'idle') metrics.refresh() }, 5000))
  timers.push(setInterval(() => { if (ui.viewMode === 'pro' && sim.status !== 'idle') metrics.loadHistory('avg_speed') }, 15000))
})
onBeforeUnmount(() => {
  timers.forEach(clearInterval)
  window.removeEventListener('resize', onResize)
})

// ── 面板拖拽调整（左右栏宽 / 上下区高） ─────────────────────
// 关键：拖拽开始时快照各面板初始值，位移从快照计算——
// 若把"全量位移"加到已更新的当前值上会叠加放大（越拖越灵敏）
const SENSITIVITY = 1

function startDrag(kind, ev) {
  ev.preventDefault()
  drag = {
    kind,
    startX: ev.clientX,
    startY: ev.clientY,
    snap: {
      leftW: ui.leftW, rightW: ui.rightW, bottomH: ui.bottomH,
      metricsH: ui.metricsH, schemeH: ui.schemeH,
    },
  }
  document.body.style.userSelect = 'none'
  document.body.style.cursor = kind === 'bottom' ? 'row-resize' : 'col-resize'
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
}
function onDragMove(e) {
  if (!drag) return
  const dx = (e.clientX - drag.startX) * SENSITIVITY
  const dy = (e.clientY - drag.startY) * SENSITIVITY
  if (drag.kind === 'left') ui.setLeftW(drag.snap.leftW + dx)
  else if (drag.kind === 'right') ui.setRightW(drag.snap.rightW - dx)
  else if (drag.kind === 'bottom') ui.setBottomH(drag.snap.bottomH - dy)
  else if (drag.kind === 'metrics') ui.setMetricsH(drag.snap.metricsH + dy)
  else if (drag.kind === 'scheme') ui.setSchemeH(drag.snap.schemeH + dy)
}
function onDragEnd() {
  drag = null
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
}
function resetPanel(kind) {
  ui.resetPanel(kind)
}

function handleStart({ net, routes, addFiles, scheme, scenario }) {
  const s = scheme || 'scheme_2'
  // 方案特定默认参数：官方方案走三档配时切换（auto 启发式选档，60s 决策）；
  // 方案二(MAPPO)沿用 agnostic 权重；其余方案空参数即可
  const schemeParams = s === 'official'
    ? { mode: 'auto', decision_step: 60 }
    : s === 'scheme_2'
      ? { mode: 'auto', obs_mode: 'agnostic', mappo_weights: 'models/weights/mappo_agnostic_full', stgcn_weights: 'models/weights/stgcn.pt' }
      : {}
  sim.start({
    netPath: net.net_path,
    routes,
    addFiles,
    scheme: s,
    schemeParams,
    rightTurnGreen: !!ui.settings.rightTurnGreen,
    scenario: scenario ?? 'normal',   // 默认平峰车流（'' 渐入由用户显式选择）
  }).catch((e) => console.warn('[start] 启动失败:', e.message))
}
function handleStop() { sim.stop().then(() => metrics.resetSession()).catch(() => {}) }
function handlePause() { sim.pause().catch(() => {}) }
function handleResume() { sim.resume().catch(() => {}) }
function handleSpeed(v) { sim.setSpeed(v).catch(() => {}) }

// ── 右转常绿：运行中实时生效 ────────────────────────────────
// 启动时已由 start 请求带 right_turn_green 应用；运行中再切换开关时，
// 直接调后端实时应用/还原（无需重启仿真）。用 immediate:true 兜底同步。
watch(() => ui.settings.rightTurnGreen, (on) => {
  if (sim.status === 'running' || sim.status === 'paused') {
    sim.setRightTurnGreen(on).catch(() => {})
  }
})
</script>

<template>
  <div class="shell">
    <TopBar
      :busy="sim.starting"
      @start="handleStart" @stop="handleStop" @pause="handlePause" @resume="handleResume"
      @speed="handleSpeed"
      @toggle-view="ui.setViewMode(ui.viewMode === 'pro' ? 'normal' : 'pro')"
      @open-settings="settingsOpen = true"
    />

    <main class="layout" :class="{ compact, narrow }">
      <aside class="left" :class="{ drawer: narrow, open: leftOpen }" :style="leftStyle">
        <div v-if="narrow" class="drawer-head">
          <span>侧边栏</span>
          <button class="drawer-close" title="收起侧边栏" @click="leftOpen = false">×</button>
        </div>
        <!-- 模式一：堆叠（默认）——三个子栏同时显示 -->
        <template v-if="ui.settings.leftMode === 'stacked'">
          <MetricsPanel :style="{ height: ui.metricsH + 'px' }" />
          <template v-if="ui.viewMode === 'pro'">
            <div class="h-handle" title="拖拽调整指标区高度（双击复位）"
              @mousedown="startDrag('metrics', $event)" @dblclick="resetPanel('metrics')" />
            <SchemePanel :style="{ height: ui.schemeH + 'px' }" />
            <div class="h-handle" title="拖拽调整方案区高度（双击复位）"
              @mousedown="startDrag('scheme', $event)" @dblclick="resetPanel('scheme')" />
            <EventPanel class="left-rest" />
          </template>
        </template>

        <!-- 模式二：单栏切换——通过页签一次只显示一个子栏 -->
        <template v-else>
          <div class="left-tabs">
            <button v-for="t in leftTabs" :key="t.key" class="ltab"
              :class="{ on: leftTab === t.key }"
              @click="ui.setSetting('leftTab', t.key)">{{ t.label }}</button>
          </div>
          <MetricsPanel v-show="leftTab === 'metrics'" class="left-rest" />
          <SchemePanel v-if="ui.viewMode === 'pro'" v-show="leftTab === 'scheme'" class="left-rest" />
          <EventPanel v-if="ui.viewMode === 'pro'" v-show="leftTab === 'event'" class="left-rest" />
        </template>
      </aside>

      <div class="v-handle" :class="{ hidden: narrow }" title="拖拽调整左栏宽度（双击复位）"
        @mousedown="startDrag('left', $event)" @dblclick="resetPanel('left')" />

      <NetCanvas class="center" />

      <div class="v-handle" :class="{ hidden: compact }" title="拖拽调整右栏宽度（双击复位）"
        @mousedown="startDrag('right', $event)" @dblclick="resetPanel('right')" />

      <aside class="right" :class="{ drawer: compact, open: rightOpen }" :style="rightStyle">
        <div v-if="compact" class="drawer-head">
          <span>AI 助手</span>
          <button class="drawer-close" title="收起 AI 助手" @click="rightOpen = false">×</button>
        </div>
        <AgentPanel />
      </aside>

      <!-- 抽屉关闭时的侧缘开合标签 -->
      <button v-if="narrow && !leftOpen" class="drawer-tab left" title="打开侧边栏" @click="leftOpen = true">›</button>
      <button v-if="compact && !rightOpen" class="drawer-tab right" title="打开 AI 助手" @click="rightOpen = true">‹</button>
    </main>

    <template v-if="ui.viewMode === 'pro'">
      <div class="h-handle" title="拖拽调整对比区高度（双击复位）"
        @mousedown="startDrag('bottom', $event)" @dblclick="resetPanel('bottom')" />
      <CompareSection :height="ui.bottomH" />
    </template>

    <SettingsPanel :open="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<style scoped>
.shell { display: flex; flex-direction: column; height: 100%; }
.layout { flex: 1; min-height: 0; display: flex; position: relative; }
.left {
  display: flex; flex-direction: column; gap: var(--space-3);
  padding: var(--space-3); overflow: hidden;
}
.left-rest { flex: 1; min-height: 0; }
.left-tabs {
  display: flex; gap: 4px; flex: 0 0 auto;
  padding: 3px; border: 1px solid var(--border);
  border-radius: var(--radius-ctrl); background: var(--bg-elev);
}
.ltab {
  flex: 1; height: 24px; border: none; border-radius: var(--radius-ctrl);
  background: transparent; color: var(--text-2); font-size: 12px; cursor: pointer;
}
.ltab:hover { color: var(--text-1); }
.ltab.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.center { flex: 1; min-width: 0; }
.right { padding: var(--space-3); overflow: hidden; }

/* ── 响应式：窄屏侧栏切换为悬浮抽屉（覆盖在画布上，开合有过渡） ── */
.layout.compact .right,
.layout.narrow .left {
  position: absolute; top: 0; bottom: 0; z-index: 20;
  display: flex; flex-direction: column;
  box-shadow: var(--shadow-2);
  transition: transform var(--dur-med) var(--ease-out);
}
.layout.narrow .left { left: 0; transform: translateX(-100%); }
.layout.narrow .left.open { transform: translateX(0); }
.layout.compact .right { right: 0; transform: translateX(100%); }
.layout.compact .right.open { transform: translateX(0); }
.v-handle.hidden { display: none; }
.drawer-head {
  flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between;
  height: 32px; padding: 0 var(--space-2);
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: var(--text-2);
}
.drawer-close {
  width: 24px; height: 24px; border: 1px solid var(--border);
  border-radius: var(--radius-ctrl); background: var(--bg-elev);
  color: var(--text-2); cursor: pointer; font-size: 14px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
}
.drawer-close:hover { color: var(--accent); border-color: var(--accent); }
.drawer-tab {
  position: absolute; z-index: 15; top: 50%; transform: translateY(-50%);
  width: 18px; height: 60px; border: 1px solid var(--border-strong);
  background: var(--bg-panel); color: var(--text-2); cursor: pointer;
  font-size: 14px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: var(--radius-ctrl);
}
.drawer-tab:hover { color: var(--accent); border-color: var(--accent); }
.drawer-tab.left { left: 0; }
.drawer-tab.right { right: 0; }

/* 拖拽手柄 */
.v-handle {
  flex: 0 0 5px; margin: 0 -1px; cursor: col-resize;
  background: transparent; border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  transition: background var(--dur-fast);
}
.v-handle:hover, .v-handle:active { background: var(--accent-soft); }
.h-handle {
  flex: 0 0 7px; cursor: row-resize;
  background: transparent; border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  position: relative;
  transition: background var(--dur-fast);
}
.h-handle::after {
  content: ''; position: absolute; left: 50%; top: 2px; transform: translateX(-50%);
  width: 36px; height: 3px; border-radius: 2px; background: var(--border-strong);
}
.h-handle:hover::after, .h-handle:active::after { background: var(--accent); }
.h-handle:hover, .h-handle:active { background: var(--accent-soft); }
</style>
