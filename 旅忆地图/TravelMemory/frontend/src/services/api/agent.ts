/** SSE 流式问答客户端。
 *
 * ## 为什么不用 EventSource
 *
 * `EventSource` 只能发 GET，而一次问答要带「问题 + 会话 id + 历史」的 JSON body。
 * 所以后端是 POST + text/event-stream，前端用 fetch 的 ReadableStream 自己切分。
 *
 * ## 两个容易写错的地方
 *
 * 1. **必须按 `\n\n` 切帧，不能按行处理** —— 一个 chunk 里可能装半帧，也可能装三帧。
 *    所以要用 buffer 累积，切完剩下的留着下次用。
 * 2. **`AbortController` 就是"停止生成"** —— abort 后 fetch 会抛 AbortError，
 *    后端检测到连接断开也会停止调用 LLM，不会继续烧 token。
 */

import type { AgentEvent } from '../../types/agent';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export interface ChatRequestBody {
  query: string;
  session_id?: string;
  history?: Array<{ role: string; content: string }>;
}

function parseFrame(raw: string): AgentEvent | null {
  let name = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) continue; // 心跳注释行
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  const rawData = dataLines.join('\n');
  let payload: unknown;
  try {
    payload = JSON.parse(rawData);
  } catch {
    // 解析不了就当纯文本片段，总好过整条流崩掉
    payload = rawData;
  }
  return { type: name, payload } as AgentEvent;
}

/** 逐事件产出后端推送的 SSE 帧。 */
export async function* streamChat(
  body: ChatRequestBody,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`${API_BASE}/api/v1/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`问答请求失败：HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep = buffer.indexOf('\n\n');
      while (sep >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const event = parseFrame(frame);
        if (event) yield event;
        sep = buffer.indexOf('\n\n');
      }
    }
  } finally {
    reader.releaseLock();
  }
}
