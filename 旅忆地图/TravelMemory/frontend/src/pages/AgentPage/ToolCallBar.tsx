import type { ToolResultPayload, ToolStartPayload } from '../../types/agent';

/** 工具名称 → 人类可读的中文标签。
 *
 * 直接显示 `search_poi` 这种内部名对开发方便、对用户是噪音；
 * 但**只在展示层翻译**，事件里仍然传原始名 —— 排查问题时你要能在界面上
 * 一眼对上后端的日志，翻译后的名字反而会挡住这件事。
 */
const TOOL_LABELS: Record<string, string> = {
  search_poi: '查地点',
  calculate_route: '算路线',
};

export function toolLabel(tool: string | null): string {
  if (!tool) return '地图工具';
  return TOOL_LABELS[tool] ?? tool;
}

/** 工具状态条：出现在**首个 delta 之前**，用来解释"这几秒在干什么"。 */
export function ToolCallBar({
  start,
  result,
}: {
  start?: ToolStartPayload;
  result?: ToolResultPayload;
}) {
  if (!start) return null;

  // 已出结果 → 不再显示"正在查"，避免状态条一直挂着让人以为卡住
  if (result) return null;

  if (!start.tool) {
    // 后端判定无需调用工具：不显示状态条，否则每句话都挂一个"没查地图"很吵
    return null;
  }

  return (
    <div className="tool-bar">
      <span className="tool-spinner" aria-hidden />
      <span>正在{toolLabel(start.tool)}…</span>
      {start.reason && <span className="tool-reason">{start.reason}</span>}
    </div>
  );
}
