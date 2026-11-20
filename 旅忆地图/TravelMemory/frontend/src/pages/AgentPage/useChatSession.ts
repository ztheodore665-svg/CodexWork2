/** 问答会话的**状态机**（不渲染任何东西）。
 *
 * ## 为什么从组件里抽出来
 *
 * 原来的流式逻辑写在 `AgentPage.tsx` 里，跟着"一整页"的样式一起。产品化之后
 * 同一个问答要出现在右下角 AI 抽屉里（主用法），将来也可能有别的入口。
 * 逻辑放组件里就得复制一份，而**流式状态机是最不该复制的东西** ——
 * `restart` 事件要清空已渲染内容、`AbortError` 不算错误、`delta` 要追加而不是
 * 替换，这三条各错一次都会表现为"答案莫名其妙少了半句"。
 *
 * ## 会话历史的落点
 *
 * 服务端 `POST /api/v1/agent/chat` 只收 `{query}`（无 session id，见
 * `services/api/agent.ts`），多轮上下文靠前端把 `history` 一起带上去。
 * 所以"历史列表"只能存在客户端：`chatHistory.ts` 负责持久化，
 * 本 hook 负责在发送/完成时写回。
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { streamChat } from '../../services/api/agent';
import type {
  ChatMessage,
  EvidenceBlock,
  ParsedPayload,
  ToolResultPayload,
  ToolStartPayload,
} from '../../types/agent';
import {
  appendMessage,
  loadConversations,
  newConversation,
  saveConversations,
  toStoredMessages,
  upsertConversation,
  type ChatConversation,
} from './chatHistory';

export interface UseChatSession {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  /** 当前会话 id。 */
  conversationId: string;
  /** 历史会话列表（最近聊的在最前）。 */
  conversations: ChatConversation[];
  send: (query: string) => Promise<void>;
  stop: () => void;
  /** 开一轮全新的对话（不清历史）。 */
  reset: () => void;
  /** 切到某条历史会话。 */
  openConversation: (id: string) => void;
  /** 删一条历史会话。 */
  removeConversation: (id: string) => void;
}

/** 带上下的最多轮数。服务端无会话态，历史全靠前端带 —— 带太多会把
 *  请求体撑到几万 token，而模型能看到的有效上下文也就那么多。 */
const HISTORY_TURNS = 8;

export function useChatSession(): UseChatSession {
  const [conversation, setConversation] = useState<ChatConversation>(() => newConversation());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ChatConversation[]>(() => loadConversations());

  const abortRef = useRef<AbortController | null>(null);
  /** 抽屉卸载后别再写历史：那会把一次没人看到的对话存进列表。 */
  const mountedRef = useRef(true);
  /** 最新 messages 的镜像。
   *
   *  `finally` 里要拿"这一轮最终的 messages"去落库，但那是闭包外的旧值 ——
   *  用 ref 而不是在 `setMessages` 的 updater 里做副作用：updater 在
   *  StrictMode 下会被调用两次，落库也会跑两遍。 */
  const messagesRef = useRef<ChatMessage[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const persist = useCallback(
    (conv: ChatConversation) => {
      const stored = toStoredMessages(messagesRef.current);
      if (stored.length === 0) return;
      // 标题规则复用 appendMessage：第一条用户消息定标题，之后不再改
      const titled = appendMessage({ ...conv, messages: [] }, stored[0]);
      const final: ChatConversation = {
        ...titled,
        messages: stored.map((message) => ({ ...message })),
      };
      setConversations((prev) => {
        const next = upsertConversation(prev, final);
        saveConversations(next);
        return next;
      });
    },
    [],
  );

  const patchLast = useCallback((patch: Partial<ChatMessage>) => {
    setMessages((prev) => {
      if (!prev.length) return prev;
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });
  }, []);

  const send = useCallback(
    async (query: string) => {
      const text = query.trim();
      if (!text || streaming) return;

      setError(null);
      setStreaming(true);

      const userMessage: ChatMessage = { id: `u-${Date.now()}`, role: 'user', content: text };
      const assistantId = `a-${Date.now()}`;
      const outgoing = [...messagesRef.current, userMessage];
      setMessages([...outgoing, { id: assistantId, role: 'assistant', content: '' }]);

      const controller = new AbortController();
      abortRef.current = controller;

      // 多轮上下文：服务端无会话态，历史由前端带上（见文件头）
      const history = outgoing.slice(-HISTORY_TURNS).map((message) => ({
        role: message.role,
        content: message.content,
      }));

      try {
        for await (const event of streamChat({ query: text, history }, controller.signal)) {
          switch (event.type) {
            case 'parsed':
              patchLast({ parsed: event.payload as unknown as ParsedPayload });
              break;
            case 'tool_start':
              patchLast({ toolStart: event.payload as ToolStartPayload });
              break;
            case 'tool_result':
              patchLast({ toolResult: event.payload as ToolResultPayload });
              break;
            case 'evidence':
              patchLast({ evidence: (event.payload as { blocks: EvidenceBlock[] }).blocks });
              break;
            case 'delta':
              // 逐段追加 —— 这就是"首字可见"的全部秘密
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                next[next.length - 1] = {
                  ...last,
                  content: last.content + (event.payload as string),
                };
                return next;
              });
              break;
            case 'restart':
              // self_check 未通过：后端要重生成，前端必须把已渲染的内容丢掉
              patchLast({ content: '' });
              break;
            case 'done':
              patchLast({ done: event.payload as ChatMessage['done'] });
              break;
            case 'error':
              setError((event.payload as { message: string }).message);
              break;
            default:
              break;
          }
        }
      } catch (exc) {
        const err = exc as Error;
        // AbortError 是用户主动点「停止」，不是错误 —— 保留已生成的部分，不报错
        if (err.name !== 'AbortError') setError(err.message ?? String(exc));
      } finally {
        setStreaming(false);
        abortRef.current = null;
        // 按"停止"打断的那半句答案也有价值，所以成功/中断都落库
        if (mountedRef.current) persist(conversation);
      }
    },
    [conversation, patchLast, persist, streaming],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setError(null);
    setConversation(newConversation());
  }, []);

  const openConversation = useCallback((id: string) => {
    abortRef.current?.abort();
    const found = loadConversations().find((item) => item.id === id);
    if (!found) return;
    setConversation(found);
    setMessages(found.messages.map((message) => ({ ...message })));
    setError(null);
  }, []);

  const removeConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const next = prev.filter((item) => item.id !== id);
      saveConversations(next);
      return next;
    });
  }, []);

  return {
    messages,
    streaming,
    error,
    conversationId: conversation.id,
    conversations,
    send,
    stop,
    reset,
    openConversation,
    removeConversation,
  };
}
