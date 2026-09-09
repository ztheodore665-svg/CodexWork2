import { defineStore } from 'pinia'
import { apiGet } from '../api/http'

/** 实时指标 + 历史序列（WS metrics_update / simulation_step 驱动 + REST 兜底） */
export const useMetricsStore = defineStore('metrics', {
  state: () => ({
    overall: { vehicle_count: 0, avg_speed: 0, avg_waiting_time: 0, avg_queue_length: 0, total_throughput: 0 },
    prev: { vehicle_count: 0, avg_speed: 0, avg_waiting_time: 0 },
    perTls: {},
    history: { step: [], series: {} },   // step: number[]，series[metric]: number[]（按 step 对齐）
  }),
  getters: {
    speedTrend: (s) => Math.sign(s.overall.avg_speed - s.prev.avg_speed),
    waitTrend: (s) => Math.sign(s.overall.avg_waiting_time - s.prev.avg_waiting_time),
  },
  actions: {
    applyWs(data) {
      if (!data) return
      if (data.overall) {
        this.prev = { ...this.overall }
        this.overall = { ...this.overall, ...data.overall }
      }
      if (data.intersections) this.perTls = data.intersections
    },
    applyStep(data) {
      if (!data) return
      if (typeof data.vehicle_count === 'number') this.overall.vehicle_count = data.vehicle_count
      if (typeof data.avg_speed === 'number') this.overall.avg_speed = data.avg_speed
    },
    async refresh() {
      try {
        const m = await apiGet('/metrics/realtime')
        if (m.overall) {
          this.prev = { ...this.overall }
          this.overall = { ...this.overall, ...m.overall }
        }
        this.perTls = m.intersections || m.per_tls || {}
      } catch { /* 静默 */ }
    },
    /** 会话结束（停止/新开页面）时清空历史与实时缓存，避免新旧会话 step 去重冲突 */
    resetSession() {
      this.history = { step: [], series: {} }
      this.overall = { vehicle_count: 0, avg_speed: 0, avg_waiting_time: 0, avg_queue_length: 0, total_throughput: 0 }
      this.prev = { ...this.overall }
      this.perTls = {}
    },
    /** 拉取并合并历史序列（按 step 去重，series 与 step 对齐） */
    async loadHistory(metric = 'avg_speed', start = 0, end = 0, interval = 10) {
      try {
        const rows = await apiGet(
          `/metrics/history?metric=${metric}&start=${start}&end=${end}&interval=${interval}`)
        const known = new Set(this.history.step)
        for (const r of rows) if (!known.has(r.step)) { known.add(r.step); this.history.step.push(r.step) }
        const byStep = {}
        for (const r of rows) byStep[r.step] = r.value
        const arr = this.history.step.map((s) => byStep[s] ?? null)
        if (arr.length > 900) { this.history.step = this.history.step.slice(-900); }
        this.history.series[metric] = arr.length > 900 ? arr.slice(-900) : arr
      } catch { /* 静默 */ }
    },
  },
})
