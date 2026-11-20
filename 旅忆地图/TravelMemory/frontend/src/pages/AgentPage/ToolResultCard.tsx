import type { ToolResultPayload } from '../../types/agent';
import { toolLabel } from './ToolCallBar';

/** 工具结果卡片（阶段 3 W3-2）。
 *
 * ## 顶部那行标注为什么是硬要求
 *
 * 工具查到的事实**不是用户的照片记忆**。不标注的话，用户会以为
 * "乌镇东栅景区"是从自己的照片里认出来的 —— 那是在伪造记忆的来源。
 * 引用率 100% 保证的是"答案有出处"，不保证"用户理解出处是什么"。
 *
 * ## 为什么失败也要显示
 *
 * 工具失败后回答仍在继续（只是只用记忆）。如果界面什么都不显示，
 * 用户会以为"查过地图了"。明确写出"工具不可用"才是对降级负责。
 */
export function ToolResultCard({ result }: { result?: ToolResultPayload }) {
  if (!result || !result.tool) return null;

  if (!result.ok) {
    return (
      <div className="tool-card tool-card-failed">
        <div className="tool-card-head">
          <span className="tool-badge">{toolLabel(result.tool)}</span>
          <span className="tool-flag">工具本次不可用</span>
        </div>
        <p className="tool-card-note">
          回答将只依据你的照片与行程记录{result.error ? `（${result.error}）` : ''}。
        </p>
      </div>
    );
  }

  return (
    <div className="tool-card">
      <div className="tool-card-head">
        <span className="tool-badge">{toolLabel(result.tool)}</span>
        <span className="tool-flag tool-flag-source">来自高德地图，不是你的照片记录</span>
        {result.cache_hit && <span className="tool-chip">缓存命中</span>}
        <span className="tool-chip">{result.latency_ms}ms</span>
      </div>
      <p className="tool-card-note">
        已并入下方证据，共 {result.evidence_count} 条，可被引用。
      </p>
    </div>
  );
}
