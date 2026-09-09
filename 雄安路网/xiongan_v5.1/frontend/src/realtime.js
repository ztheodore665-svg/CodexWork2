import { WsClient } from './api/ws'

/** 全局 WebSocket 单例：消息按 type 分发给订阅者（画布/指标/控制条共用一条连接） */
const listeners = new Map() // type -> Set<fn>

let client = null
let started = false

export function subscribe(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set())
  listeners.get(type).add(fn)
  return () => listeners.get(type)?.delete(fn)
}

function dispatch(msg) {
  if (!msg || !msg.type) return
  const set = listeners.get(msg.type)
  if (set) for (const fn of set) {
    try { fn(msg.data, msg) } catch (e) { console.warn('[ws]', msg.type, e) }
  }
}

export function initRealtime() {
  if (started) return
  started = true
  client = new WsClient(dispatch)
  client.connect()
}

export function closeRealtime() {
  started = false
  client?.close()
  client = null
}
