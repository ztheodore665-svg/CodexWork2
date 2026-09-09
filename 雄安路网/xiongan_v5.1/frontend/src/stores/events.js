import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api/http'

/** 扰动事件 */
export const useEventStore = defineStore('events', {
  state: () => ({ list: [] }),
  actions: {
    async refresh() {
      try { this.list = await apiGet('/events/list') } catch { /* 静默 */ }
    },
    async inject(eventType, params = {}) {
      await apiPost('/events/inject', { event_type: eventType, params })
      await this.refresh()
    },
  },
})
