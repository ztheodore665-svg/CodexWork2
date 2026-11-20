/** AI 助手面板（地图右下角抽屉的内容）。
 *
 * ## 与原来的 `AgentPage` 有什么不同
 *
 * 原来是一整页：顶部大标题、`max-w-860px` 居中、`min-height:100vh`。
 * 产品化之后地图是全屏底层，问答变成**叠在地图上的一块抽屉**，所以：
 *   - 不再有页面级标题（抽屉头部已经有"旅忆助手"）；
 *   - 高度由抽屉决定，内容区自己滚（`ScrollArea`），不再 `min-height:100vh`；
 *   - 输入框从 `<input>` 换成 `<textarea>`：问题经常是一长句，
 *     单行输入会把长问题横向滚出视野，发出去前没法通读。
 *
 * ## 气泡方向
 *
 * 用户居右用主色，AI 居左用中性色。这不是审美偏好而是**可扫读性**：
 * 一问一答交错出现时，方向一致才能让眼睛在"我问了什么"和"它答了什么"之间
 * 形成固定跳转，混排会让长对话完全没法读。
 *
 * ## 证据 → 地图
 *
 * 点 `[n]` 会调 `onLocate(ref)` 让地图飞到对应位置并高亮。
 * 证据里**没有坐标**（见 `lib/mapView.ts` 的说明），所以命中是尽力而为：
 * 命不中由调用方提示，本组件只负责把意图传出去。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { History, RotateCcw, Send, Sparkles, Square } from 'lucide-react';

import type { EvidenceBlock, ParsedPayload } from '../../types/agent';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { ScrollArea } from '../../components/ui/scroll-area';
import { Textarea } from '../../components/ui/textarea';
import { useChatSession } from './useChatSession';
import { ToolCallBar } from './ToolCallBar';
import { ToolResultCard } from './ToolResultCard';

export interface ChatPanelProps {
  /** 点证据 `[n]` 时通知地图定位。 */
  onLocate?: (ref: string) => void;
}

export function ChatPanel({ onLocate }: ChatPanelProps) {
  const { messages, streaming, error, conversations, send, stop, reset, openConversation, removeConversation } =
    useChatSession();
  const [input, setInput] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 新消息到达时滚到底。依赖 messages 而不是 streaming：
  // delta 是逐段来的，只有 content 变了才需要重滚。
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');
    void send(text);
  }, [input, send, streaming]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 头部：新对话 + 历史记录。历史用嵌套 Sheet 会互相抢遮罩层级，
          所以这里做成面板内的内联列表切换，不叠第二层。 */}
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Sparkles className="h-4 w-4" />
          </span>
          <span className="text-sm font-semibold">旅忆助手</span>
          <Badge variant="muted">本地检索</Badge>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
          >
            <History className="h-4 w-4" />
            历史记录
          </Button>
          <Button variant="ghost" size="sm" onClick={reset} title="开一轮新对话（不清历史）">
            <RotateCcw className="h-4 w-4" />
            新对话
          </Button>
        </div>
      </div>

      {historyOpen && (
        <div className="border-b bg-muted/40 px-4 py-3">
          {conversations.length === 0 ? (
            <p className="text-xs text-muted-foreground">还没有历史对话。</p>
          ) : (
            <ul className="max-h-52 space-y-1 overflow-y-auto">
              {conversations.map((item) => (
                <li key={item.id} className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex-1 truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-accent"
                    onClick={() => {
                      openConversation(item.id);
                      setHistoryOpen(false);
                    }}
                    title={item.title}
                  >
                    {item.title}
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 shrink-0 p-0 text-muted-foreground hover:text-destructive"
                    aria-label={`删除会话 ${item.title}`}
                    onClick={() => removeConversation(item.id)}
                  >
                    ×
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 消息区：自己滚，不撑开抽屉 */}
      <ScrollArea className="min-h-0 flex-1 px-4">
        <div ref={scrollRef} className="space-y-3 py-4">
          {messages.length === 0 && (
            <p className="text-sm text-muted-foreground">
              试试问：「我去年秋天去过哪些古镇？」「前年五一我在哪？」「我在杭州最喜欢哪类地点？」
            </p>
          )}
          {messages.map((message) => (
            <div
              key={message.id}
              className={`flex flex-col gap-1 ${message.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              <div
                className={
                  message.role === 'user'
                    ? 'max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground shadow-sm'
                    : 'max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-sm text-foreground shadow-sm'
                }
              >
                <span className="whitespace-pre-wrap break-words">{message.content}</span>
                {streaming && message.id.startsWith('a-') && message.role === 'assistant' && (
                  <span className="caret" />
                )}
              </div>

              {message.role === 'assistant' && (
                <ToolCallBar start={message.toolStart} result={message.toolResult} />
              )}
              {message.role === 'assistant' && message.parsed && (
                <FilterChips parsed={message.parsed} />
              )}
              {message.role === 'assistant' && message.toolResult && (
                <ToolResultCard result={message.toolResult} />
              )}
              {message.role === 'assistant' && message.done && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{message.done.latency_ms}ms</span>
                  {message.done.refused && (
                    <Badge variant="destructive">已拒答：{message.done.refuse_reason}</Badge>
                  )}
                  {message.done.citations.length > 0 && <span>引用 {message.done.citations.length} 条</span>}
                </div>
              )}
              {message.role === 'assistant' && message.evidence && (
                <EvidenceList blocks={message.evidence} onLocate={onLocate} />
              )}
            </div>
          ))}
        </div>
      </ScrollArea>

      {error && <div className="mx-4 mb-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">出错了：{error}</div>}

      {/* 输入区：Enter 发送，Shift+Enter 换行。
          中文输入法组词期间的 Enter 不能发送 —— 那是选字，不是提交。 */}
      <div className="border-t px-4 py-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(evt) => setInput(evt.target.value)}
            onKeyDown={(evt) => {
              if (evt.key === 'Enter' && !evt.shiftKey && !evt.nativeEvent.isComposing) {
                evt.preventDefault();
                submit();
              }
            }}
            placeholder="问我关于你旅行记忆的问题…（Enter 发送，Shift+Enter 换行）"
            className="min-h-[44px] resize-none"
            disabled={streaming}
          />
          {streaming ? (
            <Button variant="outline" onClick={stop} className="shrink-0">
              <Square className="h-4 w-4" />
              停止
            </Button>
          ) : (
            <Button onClick={submit} disabled={!input.trim()} className="shrink-0">
              <Send className="h-4 w-4" />
              发送
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function FilterChips({ parsed }: { parsed: ParsedPayload }) {
  const chips: string[] = [];
  if (parsed.time_range) {
    chips.push(`时间 ${parsed.time_range.start} ~ ${parsed.time_range.end}（${parsed.time_source}）`);
  }
  for (const place of parsed.places ?? []) chips.push(`地点 ${place}`);
  for (const theme of parsed.themes ?? []) chips.push(`主题 ${theme}`);
  if (!chips.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">我理解了：</span>
      {chips.map((chip) => (
        <Badge key={chip} variant="secondary">
          {chip}
        </Badge>
      ))}
    </div>
  );
}

/** 证据列表：`[n]` 可点，点了让地图飞过去。
 *
 *  与旧版 `EvidenceList` 的差别只在多了 `onLocate`。旧行为（展开看原文）
 *  完整保留 —— 那本来就是"引用能对应到原文"的基础链路。 */
function EvidenceList({
  blocks,
  onLocate,
}: {
  blocks: EvidenceBlock[];
  onLocate?: (ref: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!blocks.length) return null;

  return (
    <div className="w-full">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setOpen((v) => !v)}>
          {open ? '收起' : '展开'}证据（{blocks.length}）
        </Button>
        {onLocate && blocks.length === 1 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onLocate(blocks[0].ref)}
            title="在地图上定位这条证据"
          >
            定位到地图
          </Button>
        )}
      </div>
      {open && (
        <ol className="mt-1 space-y-1.5">
          {blocks.map((block) => (
            <li key={block.ref} className="rounded-md border bg-background px-2 py-1.5 text-xs">
              <div className="flex items-center gap-2">
                {onLocate ? (
                  <button
                    type="button"
                    className="font-mono text-primary underline-offset-2 hover:underline"
                    onClick={() => onLocate(block.ref)}
                    title="在地图上定位这条证据"
                  >
                    [{block.index}]
                  </button>
                ) : (
                  <code>[{block.index}]</code>
                )}{' '}
                <span className="truncate text-muted-foreground">{block.ref}</span>
                <span className="ml-auto shrink-0 text-muted-foreground">
                  score={block.score.toFixed(3)}
                </span>
              </div>
              <p className="mt-1 leading-relaxed">{block.content}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
