/** WebSocket 客户端：/ws 增量消息分发 */
export class WsClient {
  constructor(onMessage) {
    this.onMessage = onMessage
    this.ws = null
    this.retry = 0
    this.closed = false
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws.onopen = () => { this.retry = 0 }
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        this.onMessage?.(msg)
      } catch { /* 忽略非 JSON */ }
    }
    this.ws.onclose = () => {
      if (!this.closed && this.retry < 5) {
        this.retry += 1
        setTimeout(() => this.connect(), 1000 * this.retry)
      }
    }
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
