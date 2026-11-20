/** 与后端 app/schemas/agent.py + pipeline 事件一一对应的类型。
 *
 * 为什么前端也要再定义一遍：TypeScript 与 Python 不在同一套类型系统里，
 * 手写一份可以让「后端改了事件结构」在编译期就暴露，而不是等到运行时 undefined。
 */

export interface ChatMeta {
  prompt_version: string;
  embedding_model: string;
  runtime: string;
  session_id: string;
}

export interface ParsedPayload {
  degraded: boolean;
  keywords: string[];
  places: string[];
  themes: string[];
  time_range: { start: string; end: string } | null;
  /** 时间范围是规则算出来的还是 LLM 给的 —— 排查误过滤时第一个要看的字段 */
  time_source: 'rule' | 'llm' | 'none';
  time_label: string;
}

export interface EvidenceBlock {
  index: number;
  ref: string;
  score: number;
  content: string;
}

export interface EvidencePayload {
  blocks: EvidenceBlock[];
  filter: Record<string, unknown>;
  filter_fallback: boolean;
  vector_hits: number;
  lexical_hits: number;
}

export interface DonePayload {
  refused: boolean;
  refuse_reason: string;
  citations: string[];
  latency_ms: number;
  token_usage: Record<string, number>;
}

/** 工具即将调用（阶段 3 W3-1）。
 *
 * 这个事件的意义是**消除一段没有解释的等待**：工具调用是链路里唯一会引入
 * 外部网络耗时的环节，不提示的话用户会以为卡死了。
 */
export interface ToolStartPayload {
  tool: string | null;
  reason: string;
  source: 'rule' | 'llm' | '';
}

/** 工具调用结束（阶段 3 W3-1）。
 *
 * 前端必须区分三种状态，否则会把"没查到"显示成"查到了"：
 *   ok + cache_hit → 查到了，来自缓存
 *   ok             → 查到了
 *   !ok            → 工具不可用，本次只用记忆回答
 */
export interface ToolResultPayload {
  tool: string | null;
  ok: boolean;
  error: string;
  cache_hit: boolean;
  latency_ms: number;
  evidence_count: number;
  source: string;
}

export type AgentEvent =
  | { type: 'meta'; payload: ChatMeta }
  | { type: 'parsed'; payload: ParsedPayload }
  | { type: 'tool_start'; payload: ToolStartPayload }
  | { type: 'tool_result'; payload: ToolResultPayload }
  | { type: 'evidence'; payload: EvidencePayload }
  | { type: 'delta'; payload: string }
  | { type: 'restart'; payload: { reason: string } }
  | { type: 'done'; payload: DonePayload }
  | { type: 'error'; payload: { message: string } };

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  evidence?: EvidenceBlock[];
  done?: DonePayload;
  parsed?: ParsedPayload;
  toolStart?: ToolStartPayload;
  toolResult?: ToolResultPayload;
}
