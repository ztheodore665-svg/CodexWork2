/** REST 客户端：统一 {code, data, message} 解包 */
const BASE = '/api/v1'

export async function apiGet(path, timeoutMs = 15000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(BASE + path, { signal: ctrl.signal })
    const body = await res.json()
    if (body.code !== 0) throw new Error(body.message || `HTTP ${res.status}`)
    return body.data
  } finally {
    clearTimeout(timer)
  }
}

export async function apiPost(path, payload, timeoutMs = 30000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    })
    const body = await res.json()
    if (body.code !== 0) throw new Error(body.message || `HTTP ${res.status}`)
    return body.data
  } finally {
    clearTimeout(timer)
  }
}

export async function apiUpload(path, files) {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  const res = await fetch(BASE + path, { method: 'POST', body: fd })
  const body = await res.json()
  if (body.code !== 0) throw new Error(body.message || `HTTP ${res.status}`)
  return body.data
}
