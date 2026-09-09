import { defineStore } from 'pinia'

/* 布局尺寸：固定像素 → 视口感知（流体）。任何屏幕尺寸下都保证：
   ① 左/右栏宽、上下区高随视口缩放钳制；② 指标+方案+事件高度之和不越界；
   ③ 底部对比区不挤掉侧栏最小内容。拖拽预算用单一来源常量。 */
const TOP_BAR_H = 52          // 顶栏高度（与 tokens.css --topbar-h 保持一致）
const HANDLE_H = 7            // 底部对比区上方 h-handle
const LEFT_RESERVED = 54      // 左栏内部固定占用：上下 padding(24) + 3 gap(12) + 2 handle(14)
const EVENT_PANEL_MIN = 60    // EventPanel 最小可见高度（标题 + 少量滚动空间）
const BOTTOM_MIN = 100

// 固定基准（仅作兜底/迁移起点；实际默认由 viewDefaults 按视口给出）
const BASE_DEFAULTS = { leftW: 300, rightW: 380, bottomH: 200, metricsH: 210, schemeH: 330 }

const vw = () => (typeof window !== 'undefined' ? window.innerWidth : 1440)
const vh = () => (typeof window !== 'undefined' ? window.innerHeight : 900)

/** 左栏可用高度 = 视口 - 顶栏 - 底部手柄 - 底部对比区(bottomH) */
function leftAvail(bottomH) {
  return Math.max(0, vh() - TOP_BAR_H - HANDLE_H - bottomH)
}

/** 各面板随视口缩放的钳制区间（小屏收紧、大屏放开） */
function clampRanges() {
  const W = vw(), H = vh()
  return {
    leftW: [Math.min(170, Math.round(0.15 * W)), Math.min(560, Math.round(0.36 * W))],
    rightW: [Math.min(240, Math.round(0.20 * W)), Math.min(640, Math.round(0.46 * W))],
    bottomH: [BOTTOM_MIN, Math.min(420, Math.round(0.42 * H))],
    metricsH: [120, Math.min(440, Math.round(0.42 * H))],
    schemeH: [150, Math.min(580, Math.round(0.56 * H))],
  }
}

/** 首次进入（无持久化偏好）时的视口比例默认 */
function viewDefaults() {
  const W = vw(), H = vh()
  return {
    leftW: Math.round(Math.min(300, Math.max(190, 0.20 * W))),
    rightW: Math.round(Math.min(380, Math.max(240, 0.24 * W))),
    bottomH: Math.round(Math.min(220, Math.max(120, 0.22 * H))),
    metricsH: Math.round(Math.min(230, Math.max(150, 0.22 * H))),
    schemeH: Math.round(Math.min(360, Math.max(170, 0.34 * H))),
  }
}

/** 钳制单个值 */
function clamp(key, v) {
  const [lo, hi] = clampRanges()[key]
  return Math.max(lo, Math.min(hi, Math.round(v)))
}

/** 底部对比区高度上限：还要给侧栏最小内容留空间 */
function maxBottom() {
  const R = clampRanges()
  return Math.max(BOTTOM_MIN, vh() - TOP_BAR_H - HANDLE_H - LEFT_RESERVED
    - R.metricsH[0] - R.schemeH[0] - EVENT_PANEL_MIN)
}

/** metricsH + schemeH 之和上限（给 EventPanel 留最小空间） */
function maxSum(bottomH) {
  return Math.max(0, leftAvail(bottomH) - LEFT_RESERVED - EVENT_PANEL_MIN)
}

/** 优先保指标区、压缩方案区的两区配平；二者之和不能超出 cap */
function fitPair(metricsH, schemeH, cap) {
  const R = clampRanges()
  const mLo = R.metricsH[0], mHi = R.metricsH[1]
  const sLo = R.schemeH[0], sHi = R.schemeH[1]
  let m = Math.max(mLo, Math.min(mHi, metricsH))
  let s = Math.max(sLo, Math.min(sHi, schemeH))
  if (m + s > cap) {
    s = Math.max(sLo, s - (m + s - cap))          // 先压方案区
    m = Math.max(mLo, Math.min(mHi, cap - s))     // 仍超再压指标区
    if (m + s > cap) { m = Math.max(mLo, cap - s) }
  }
  return { metricsH: m, schemeH: s }
}

function loadLayout() {
  // 评审/演示要求：每次启动都用固定默认布局（不恢复上次拖拽尺寸）
  const def = viewDefaults()
  try {
    const pair = fitPair(def.metricsH, def.schemeH, maxSum(def.bottomH))
    return { ...def, metricsH: pair.metricsH, schemeH: pair.schemeH }
  } catch {
    return { ...def }
  }
}

const SETTINGS_DEFAULTS = {
  dblClickReset: true,   // 双击画布复位视图
  showMedian: true,      // 显示中央分隔线
  showLights: true,      // 显示信号灯
  showVehicles: true,    // 显示车辆
  infoAutoRefresh: true, // 选中信息自动刷新
  showLegend: true,      // 左上角事件图例（事故/施工/突发车流）
  showEvalCharts: false, // 底部离线评估柱状图与对比表格（默认关闭）
  rightTurnGreen: false,  // 右转常绿：启动时把右转信号恒为绿灯（运行时覆写，不改相位结构）
  hideRightTurnLights: false, // 隐藏右转灯：右转常绿时隐藏右转灯头显示（子选项）
  showTurnLights: true,   // 显示左转/掉头灯：评委默认=开（最左车道有左转相位就显示左转，画面不单调）
  lightMode: 'bare',     // 信号灯样式：solid 实心圆 | framed 圆框箭头 | bare 无框箭头（评委默认）
  minimalLights: true,   // 极简模式：每车道仅显示一个方向的信号灯（评委默认，配合 bare）
  leftMode: 'tabs',      // 左栏模式：stacked 堆叠 | tabs 单栏切换（评委默认=单栏页签）
  leftTab: 'metrics',    // 单栏模式当前子栏：metrics | scheme | event
  // 指标卡默认：基础四卡 + 最堵塞道路 + 最堵塞路口 + 完成率（评委默认，顺序即显示顺序）
  customMetrics: ['most_congested_edge', 'most_congested_tls', 'completion_rate'],
}

function loadSettings() {
  // 评审/演示要求：每次启动都用固定默认（不恢复上次关闭时的设置）
  return { ...SETTINGS_DEFAULTS }
}

/** UI 状态：主题（深/浅）+ 视图（普通/专业）+ 面板尺寸 + 设置。
 *  评审要求：全部为固定默认，不持久化、不恢复上次关闭时的状态。 */
export const useUiStore = defineStore('ui', {
  state: () => ({
    theme: 'dark',
    viewMode: 'pro',             // 默认专业模式（评委）
    scenario: 'normal',          // 交通场景选择（启动参数，画布右上角选择）——默认平峰
    settings: loadSettings(),
    testMode: false,             // 测试车辆选路模式（画布点击选边）
    spotlight: null,             // 跨组件聚焦请求 {type:'vehicle'|'edge'|'node', id}（NetCanvas 监听）
    ...loadLayout(),
  }),
  actions: {
    setViewMode(mode) {
      this.viewMode = mode
    },
    setSetting(key, val) {
      this.settings[key] = val
    },
    setTestMode(v) { this.testMode = v },
    setSpotlight(sp) { this.spotlight = sp },
    setScenario(v) { this.scenario = v || '' },
    apply() {
      document.documentElement.dataset.theme = this.theme
    },

    setTheme(theme) {
      this.theme = theme
      this.apply()
    },
    toggleTheme() { this.setTheme(this.theme === 'dark' ? 'light' : 'dark') },

    setLeftW(v) { this.leftW = clamp('leftW', v); this._saveLayout() },
    setRightW(v) { this.rightW = clamp('rightW', v); this._saveLayout() },
    setBottomH(v) {
      this.bottomH = Math.max(BOTTOM_MIN, Math.min(clampRanges().bottomH[1], maxBottom(), Math.round(v)))
      this._saveLayout()
    },
    setMetricsH(v) {
      // 独立钳制：拖哪个手柄只改哪个面板，另一面板不动；上限动态跟随左栏可用高度
      const [lo, hi] = clampRanges().metricsH
      const dyn = Math.max(lo, Math.min(hi, leftAvail(this.bottomH) - LEFT_RESERVED - this.schemeH))
      this.metricsH = Math.max(lo, Math.min(dyn, Math.round(v)))
      this._saveLayout()
    },
    setSchemeH(v) {
      const [lo, hi] = clampRanges().schemeH
      const dyn = Math.max(lo, Math.min(hi, leftAvail(this.bottomH) - LEFT_RESERVED - this.metricsH))
      this.schemeH = Math.max(lo, Math.min(dyn, Math.round(v)))
      this._saveLayout()
    },
    /** 视口变化后重算所有面板尺寸（窗口 resize / 断点切换时调用） */
    reclampLayout() {
      this.leftW = clamp('leftW', this.leftW)
      this.rightW = clamp('rightW', this.rightW)
      this.bottomH = Math.max(BOTTOM_MIN, Math.min(clampRanges().bottomH[1], maxBottom(), this.bottomH))
      const pair = fitPair(this.metricsH, this.schemeH, maxSum(this.bottomH))
      this.metricsH = pair.metricsH
      this.schemeH = pair.schemeH
    },
    resetLayout() {
      const d = viewDefaults()
      this.leftW = d.leftW
      this.rightW = d.rightW
      this.bottomH = d.bottomH
      this.metricsH = d.metricsH
      this.schemeH = d.schemeH
      const pair = fitPair(this.metricsH, this.schemeH, maxSum(this.bottomH))
      this.metricsH = pair.metricsH
      this.schemeH = pair.schemeH
    },
    /** 双击某个手柄复位：恢复为该面板的视口比例默认（而非写死数值） */
    resetPanel(kind) {
      const d = viewDefaults()
      const key = { left: 'leftW', right: 'rightW', bottom: 'bottomH', metrics: 'metricsH', scheme: 'schemeH' }[kind]
      if (!key) return
      if (key === 'metricsH' || key === 'schemeH') {
        this[key] = d[key]
        const pair = fitPair(this.metricsH, this.schemeH, maxSum(this.bottomH))
        this.metricsH = pair.metricsH
        this.schemeH = pair.schemeH
      } else {
        this[key] = d[key]
      }
    },
    _saveLayout() {
      // 不再持久化：布局只保存在内存，刷新/重启即恢复固定默认
    },
  },
})
