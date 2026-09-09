import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api/http'

/** LLM Agent 对话状态 */
export const useAgentStore = defineStore('agent', {
  state: () => ({
    status: null,            // GET /agent/status
    tools: [],               // GET /agent/tools 技能清单 [{name, description, args}]
    messages: [],            // {role: 'user'|'assistant', content, toolCalls: []}
    thinking: false,
    error: null,
  }),
  actions: {
    async fetchStatus() {
      try { this.status = await apiGet('/agent/status') } catch { this.status = null }
    },
    async fetchTools() {
      try { this.tools = await apiGet('/agent/tools') } catch { this.tools = [] }
    },
    /** 运行时切换 LLM 模型（可选免费模型） */
    async switchModel(model) {
      const d = await apiPost('/agent/model', { model })
      if (d.ok && this.status) {
        this.status = { ...this.status, model: d.model, available_models: d.available_models || [] }
      }
      return d
    },
    /** 清空会话记忆与对比基线 */
    async resetMemory() {
      await apiPost('/agent/reset', {})
      this.messages = []
      this.error = null
    },
    async chat(text) {
      this.thinking = true
      this.error = null
      try {
        const d = await apiPost('/agent/chat', { message: text }, 120000)
        this.messages.push({ role: 'user', content: text, toolCalls: [] })
        this.messages.push({ role: 'assistant', content: d.reply, toolCalls: d.tool_calls || [] })
        return d
      } catch (e) {
        this.error = e.message
        throw e
      } finally {
        this.thinking = false
      }
    },
  },
})
