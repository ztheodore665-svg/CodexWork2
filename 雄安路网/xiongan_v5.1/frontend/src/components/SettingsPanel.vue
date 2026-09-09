<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useUiStore } from '../stores/ui'

defineProps({ open: Boolean })
const emit = defineEmits(['close'])
const ui = useUiStore()

// 响应式视口高度：设置窗固定为屏高的 86%，小屏幕超出部分在面板内滚动
const MAX_H_RATIO = 0.86
const vh = ref(typeof window !== 'undefined' ? window.innerHeight : 800)
const onResize = () => { vh.value = window.innerHeight }
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
const panelStyle = computed(() => ({ maxHeight: Math.round(vh.value * MAX_H_RATIO) + 'px' }))

// 开关项（全部条目式：左 label+desc，右滑动开关），顺序即面板展示顺序
const SWITCHES = [
  { key: 'dblClickReset', label: '双击画布复位视图', desc: '关闭后双击不再复位缩放/平移' },
  { key: 'infoAutoRefresh', label: '选中信息自动刷新', desc: '每 4 秒刷新选中对象的数据' },
  { key: 'showMedian', label: '显示中央分隔线', desc: '双向道路之间加宽中缝空隙 + 双黄线' },
  { key: 'showLights', label: '显示信号灯', desc: '路口逐进口信号灯' },
  { key: 'showVehicles', label: '显示车辆', desc: '关闭后可专注查看路网' },
  { key: 'showLegend', label: '显示事件图例', desc: '左上角事故/施工/突发车流颜色图例' },
  { key: 'showEvalCharts', label: '显示离线评估图表', desc: '底部离线评估柱状图与方案对比表格（默认关闭）' },
  { key: 'rightTurnGreen', label: '右转常绿', desc: '右转信号恒为绿灯（运行中切换立即生效，不改相位结构，三方案均兼容）' },
  { key: 'hideRightTurnLights', label: '隐藏右转灯', desc: '右转常绿时，画布上不再绘制右转灯头（需先开启右转常绿）' },
  { key: 'showTurnLights', label: '显示左转/掉头灯', desc: '默认开启（最左车道有左转相位就显示左转，画面更真实不单调）；关闭则只画直行主信号' },
  { key: 'minimalLights', label: '极简模式', desc: '每条车道仅显示一个主方向的信号灯（最左车道优先左转、其余直行/右转，需信号灯样式为无框箭头）' },
]

// 子选项：仅当前置开关满足时才可用
function isSubDisabled(key) {
  if (key === 'hideRightTurnLights') return !ui.settings.rightTurnGreen
  if (key === 'minimalLights') return ui.settings.lightMode !== 'bare'
  return false
}

// 切换信号灯样式：离开"无框箭头"时自动关闭极简模式（极简仅该样式可用）
function setLightMode(m) {
  ui.setSetting('lightMode', m)
  if (m !== 'bare' && ui.settings.minimalLights) ui.setSetting('minimalLights', false)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="mask" @click.self="emit('close')">
      <div class="panel" :style="panelStyle">
        <div class="head">
          <span class="title">设置</span>
          <button class="close" @click="emit('close')" title="关闭">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <div class="body">
          <!-- 界面主题 -->
          <div class="item">
            <div class="txt">
              <div class="label">界面主题</div>
              <div class="desc">深浅模式切换</div>
            </div>
            <div class="seg">
              <button :class="{ on: ui.theme === 'dark' }" @click="ui.setTheme('dark')">深色</button>
              <button :class="{ on: ui.theme === 'light' }" @click="ui.setTheme('light')">浅色</button>
            </div>
          </div>

          <!-- 左侧栏模式 -->
          <div class="item">
            <div class="txt">
              <div class="label">左侧栏模式</div>
              <div class="desc">堆叠同时显示三个子栏 / 单栏切换（一次只显示一个）</div>
            </div>
            <div class="seg">
              <button :class="{ on: ui.settings.leftMode === 'stacked' }"
                @click="ui.setSetting('leftMode', 'stacked')">堆叠</button>
              <button :class="{ on: ui.settings.leftMode === 'tabs' }"
                @click="ui.setSetting('leftMode', 'tabs')">单栏</button>
            </div>
          </div>

          <!-- 信号灯样式 -->
          <div class="item">
            <div class="txt">
              <div class="label">信号灯样式</div>
              <div class="desc">实心圆（纯色圆点）/ 圆框箭头（深色外壳内方向箭头）/ 无框箭头（纯方向箭头）</div>
            </div>
            <div class="seg">
              <button :class="{ on: ui.settings.lightMode === 'solid' }"
                @click="setLightMode('solid')">实心圆</button>
              <button :class="{ on: ui.settings.lightMode === 'framed' }"
                @click="setLightMode('framed')">圆框箭头</button>
              <button :class="{ on: ui.settings.lightMode === 'bare' }"
                @click="setLightMode('bare')">无框箭头</button>
            </div>
          </div>

          <!-- 条目式开关（全部左文右开关） -->
          <div v-for="it in SWITCHES" :key="it.key" class="item"
            :class="{ sub: isSubDisabled(it.key) }">
            <div class="txt">
              <div class="label">{{ it.label }}</div>
              <div class="desc">{{ it.desc }}</div>
            </div>
            <button class="sw" :class="{ on: ui.settings[it.key] }"
              :disabled="isSubDisabled(it.key)" :title="it.desc"
              @click="ui.setSetting(it.key, !ui.settings[it.key])">
              <span class="knob" />
            </button>
          </div>

          <!-- 重置布局 -->
          <div class="item">
            <div class="txt">
              <div class="label">重置布局</div>
              <div class="desc">面板尺寸恢复默认（左右栏、底部栏、左栏高度）</div>
            </div>
            <button class="reset" @click="ui.resetLayout()">重置</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mask {
  position: fixed; inset: 0; z-index: 100;
  background: rgb(0 0 0 / 0.45);
  display: flex; align-items: center; justify-content: center;
}
.panel {
  width: min(680px, 92vw); max-width: calc(100vw - 24px);
  max-height: 86vh;
  display: flex; flex-direction: column;
  padding: var(--space-4);
  background: var(--bg-panel); border: 1px solid var(--border-strong);
  border-radius: var(--radius-panel); box-shadow: var(--shadow-2);
}
.head { flex: 0 0 auto; display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-4); }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding-right: 2px; }
.title { font-size: 18px; font-weight: 700; }
.close {
  width: 28px; height: 28px; padding: 0; border: none; background: none;
  color: var(--text-3); display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer;
}
.close:hover { color: var(--signal-red); }
.item { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 9px 2px; border-top: 1px solid var(--border); }
.item.sub { opacity: 0.45; }
.item.sub .sw { cursor: not-allowed; }
.label { font-size: 13px; color: var(--text-1); }
.desc { font-size: 11px; color: var(--text-3); margin-top: 2px; }
/* 行式滑动开关 */
.sw {
  width: 40px; height: 22px; border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--bg-elev); cursor: pointer; position: relative; flex: 0 0 auto;
  transition: background var(--dur-fast), border-color var(--dur-fast);
}
.sw:hover:not(.on) { border-color: var(--accent); }
.sw.on { background: var(--accent); border-color: var(--accent); }
.knob {
  position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
  background: var(--text-2); transition: left var(--dur-fast) var(--ease-out), background var(--dur-fast);
}
.sw.on .knob { left: 20px; background: oklch(0.16 0.01 80); }
.reset {
  flex: 0 0 auto; height: 28px; padding: 0 12px;
  border: 1px solid var(--accent); border-radius: var(--radius-ctrl);
  background: var(--accent-soft); color: var(--accent); font-size: 12px; cursor: pointer;
}
.reset:hover { background: var(--accent); color: oklch(0.16 0.01 80); }
.seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-ctrl); overflow: hidden; flex: 0 0 auto; }
.seg button {
  height: 28px; padding: 0 10px; border: none; background: var(--bg-elev);
  color: var(--text-2); font-size: 12px; cursor: pointer; white-space: nowrap;
  transition: background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.seg button + button { border-left: 1px solid var(--border-strong); }
.seg button:hover:not(.on) { background: var(--bg-hover); color: var(--accent); }
.seg button.on { background: var(--accent); color: oklch(0.16 0.01 80); }
</style>
