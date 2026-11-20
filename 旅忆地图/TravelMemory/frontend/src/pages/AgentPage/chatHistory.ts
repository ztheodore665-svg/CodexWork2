/** 会话历史的**持久化与纯逻辑**（不碰 DOM、不碰网络）。
 *
 * ## 为什么历史要自己做，而不是等后端
 *
 * 后端 `POST /api/v1/agent/chat` 只收 `{query}`，没有 session id
 * （见 `services/api/agent.ts`）—— 服务端目前是无状态的，多轮上下文靠
 * 前端把历史一起带上去。在这种结构下，"历史列表"这件事只能落在客户端。
 *
 * ## 为什么存 localStorage 而不是 IndexedDB
 *
 * 一条会话就是若干段文本，总量在几十 KB 量级，`localStorage` 的 5MB 配额
 * 足够；上 IndexedDB 要为"按列表读 + 整条覆盖写"引入一整套异步事务，
 * 收益是负数。隐私模式下读写都 try/catch，失败就当没有历史，不阻断聊天。
 */

import type { ChatMessage } from '../../types/agent';

/** 一条落库的消息。刻意只存三字段：
 *  `ChatMessage` 还带 `evidence / toolStart / toolResult / parsed / done`，
 *  那些是**当次渲染**的中间态，存下来只会让 localStorage 体积膨胀，
 *  而重新问一次就能拿到新的。真正需要复现的是"问过什么、答过什么"。 */
export interface StoredMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatConversation {
  id: string;
  /** 取第一条用户消息的前若干个字，让历史列表能认出来。 */
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: StoredMessage[];
}

export const STORAGE_KEY = 'travelmemory.chat.v1';

/** 历史列表上限。超过就丢最旧的 —— 本地历史是便利功能，不是归档。 */
export const MAX_CONVERSATIONS = 50;

/** 可注入的存储（单测与隐私模式降级用）。 */
export type ConversationStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

let storage: ConversationStore | null = null;

function defaultStore(): ConversationStore | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    localStorage.getItem(STORAGE_KEY);
    return localStorage;
  } catch {
    return null;
  }
}

/** 注入自定义存储（单测用；`null` 回默认）。 */
export function setConversationStore(next: ConversationStore | null): void {
  storage = next;
}

function resolveStore(): ConversationStore | null {
  return storage ?? defaultStore();
}

/** 由消息列表推出会话标题。
 *
 *  空会话返回「新对话」而不是空串：历史列表里一个空标题的行是没法点的。
 *  超长截断加省略号，否则一条 200 字的问题会把列表撑成一列小作文。 */
export function conversationTitle(messages: readonly StoredMessage[], maxLength = 24): string {
  const first = messages.find((message) => message.role === 'user' && message.content.trim());
  if (!first) return '新对话';
  const text = first.content.trim().replace(/\s+/g, ' ');
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

/** 新建一条空会话。 */
export function newConversation(now: Date = new Date()): ChatConversation {
  const iso = now.toISOString();
  return {
    id: `c-${now.getTime().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    title: '新对话',
    createdAt: iso,
    updatedAt: iso,
    messages: [],
  };
}

/** 把界面上的消息折成可存储的精简版。 */
export function toStoredMessages(messages: readonly ChatMessage[]): StoredMessage[] {
  return messages
    .filter((message) => message.content.trim().length > 0)
    .map((message) => ({ id: message.id, role: message.role, content: message.content }));
}

/** 会话追加/更新一条消息，返回**新**对象（不改入参）。 */
export function appendMessage(
  conversation: ChatConversation,
  message: StoredMessage,
  now: Date = new Date(),
): ChatConversation {
  const messages = [...conversation.messages, message];
  return {
    ...conversation,
    title: conversation.title === '新对话' ? conversationTitle(messages) : conversation.title,
    updatedAt: now.toISOString(),
    messages,
  };
}

/** 读全部会话，按 `updatedAt` 倒序（最近聊的在最上面）。 */
export function loadConversations(): ChatConversation[] {
  const store = resolveStore();
  if (!store) return [];
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isConversation)
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, MAX_CONVERSATIONS);
  } catch {
    // 存储损坏：当没有历史处理。历史是便利功能，不值得为它弹错误。
    return [];
  }
}

/** 写回全部会话（含上限裁剪）。 */
export function saveConversations(conversations: readonly ChatConversation[]): void {
  const store = resolveStore();
  if (!store) return;
  try {
    const trimmed = [...conversations]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, MAX_CONVERSATIONS);
    store.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // 配额满 / 隐私模式：静默失败，聊天照常能用
  }
}

/** 把一条会话插入列表顶部（同 id 覆盖，实现"更新而非新增"）。 */
export function upsertConversation(
  conversations: readonly ChatConversation[],
  conversation: ChatConversation,
): ChatConversation[] {
  const rest = conversations.filter((item) => item.id !== conversation.id);
  return [conversation, ...rest].slice(0, MAX_CONVERSATIONS);
}

/** 删一条会话。 */
export function deleteConversation(
  conversations: readonly ChatConversation[],
  id: string,
): ChatConversation[] {
  return conversations.filter((item) => item.id !== id);
}

function isConversation(value: unknown): value is ChatConversation {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<ChatConversation>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.createdAt === 'string' &&
    typeof candidate.updatedAt === 'string' &&
    Array.isArray(candidate.messages)
  );
}
