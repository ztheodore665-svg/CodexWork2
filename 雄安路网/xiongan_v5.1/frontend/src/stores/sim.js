import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api/http'

/** 仿真状态与控制 */
export const useSimStore = defineStore('sim', {
  state: () => ({
    status: 'idle',          // idle | running | paused
    sessionId: null,
    step: 0,
    simTime: 0,
    scheme: null,            // 后端实际活动方案（refreshStatus 轮询/动作后刷新）
    schemeMode: '',          // 活动方案控制器模式（scheme_2: mappo/scoot/auto），Agent/面板切换后即时可见
    speed: 1,
    starting: false,         // 启动请求进行中
    nets: [],                // GET /networks 列表 [{name, net_path, routes[], adds[]}]
    lastNetPath: null,
    currentEdges: [],        // 当前加载路网的边 id 列表（画布解析后写入，供 Agent/事件用）
    previewNetPath: '',      // 页面初始/未启动时展示的路网 net_path（开箱即见路网）
    startScheme: 'scheme_2', // 启动方案：none | scheme_1 | scheme_2 | scheme_3
    startScenario: 'normal',     // 启动交通场景：''=渐入(路网自带) | sparse/normal/peak/extreme
    lastError: null,
  }),
  actions: {
    setPreviewNet(p) { this.previewNetPath = p || '' },
    async refreshStatus() {
      try {
        const s = await apiGet('/simulate/status')
        this.status = s.state
        this.sessionId = s.session_id
        this.step = s.step
        this.simTime = s.sim_time
        this.scheme = s.scheme
        this.schemeMode = s.scheme_mode || ''
      } catch (e) {
        // 保留上一状态：瞬时网络失败/超时不应把状态误置 idle
        //（否则 NetCanvas 会把画布清空——如暂停期间慢请求偶发超时）
        this.lastError = e.message
      }
    },
    async listNetworks() {
      try { this.nets = await apiGet('/networks') } catch { /* 后端未起 */ }
    },
    async start({ netPath, routes = [], addFiles = [], scheme = 'scheme_2', schemeParams = {}, rightTurnGreen = false, scenario = 'normal' }) {
      this.starting = true
      try {
        const d = await apiPost('/simulate/start', {
          net_path: netPath, route_files: routes, add_files: addFiles,
          scheme, scheme_params: schemeParams, right_turn_green: rightTurnGreen,
          scenario,
        })
        this.sessionId = d.session_id
        this.lastNetPath = netPath
        this.startScheme = scheme
        this.startScenario = scenario
        // 立即同步活动方案/模式（后端 start 响应已含 status）
        const st = d.status || {}
        this.scheme = st.scheme || scheme
        this.schemeMode = st.scheme_mode || ''
        this.status = 'running'
        return d
      } finally {
        this.starting = false
      }
    },
    async stop() {
      await apiPost('/simulate/stop', {})
      this.status = 'idle'
      this.sessionId = null
    },
    async pause() { await apiPost('/simulate/pause', {}) },
    async resume() { await apiPost('/simulate/resume', {}) },
    async setSpeed(v) { await apiPost('/simulate/speed', { speed: v }) },
    /** 右转常绿开关：运行中实时应用/还原（不要求重启仿真） */
    async setRightTurnGreen(enabled) {
      return apiPost('/simulate/right-turn-green', { enabled: !!enabled })
    },
    /** 方案配置动作：POST /schemes/{id}/config {action, params} */
    async schemeAction(schemeId, action, params = {}) {
      return apiPost(`/schemes/${schemeId}/config`, { action, params })
    },
    applyStep(data) {
      if (!data) return
      if (typeof data.step === 'number') this.step = data.step
      if (typeof data.simulation_time === 'number') this.simTime = data.simulation_time
    },
  },
})
