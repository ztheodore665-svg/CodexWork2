/** 地图视野与证据定位的**纯逻辑**（不碰 DOM / 不高德 API）。
 *
 * ## 为什么单独一个文件
 *
 * 地图组件（`MapCanvas.tsx`）一 import 就摸 `window.AMap`，Node 里跑不起来。
 * 而"初始视野该落在哪"、"点了一条证据该飞去哪里"这两件事是**决策**，
 * 必须有测试锁死 —— 尤其是初始视野：它是人类打开页面看到的第一屏，
 * 改错了不会报错，只会让人觉得"这地图怎么是歪的"。
 */

/** 默认视野中心：中国地理中心附近（兰州西）。
 *
 *  为什么不用 `[105, 35]`（旧值）：那是"中国大陆几何中心"的近似，但一进页面
 *  用户先看到的是新疆西藏的荒原，而绝大多数用户的记忆集中在地图右下角。
 *  `[104.195, 35.861]` 偏东一点，配合 zoom 4 能让"中国全貌 + 主要城市带"
 *  同时落在首屏里，又不会把南海诸岛挤出去。
 */
export const DEFAULT_MAP_CENTER: readonly [number, number] = [104.195, 35.861];

/** 默认缩放。3.5 看不到城市名，5 又装不下全国，4 是"能看出轮廓也能点开城市"的一档。 */
export const DEFAULT_MAP_ZOOM = 4;

/** 一个"可被定位"的地图点。刻意用结构类型而不是 import `MapPointInput`：
 *  `lib/` 是底层，不该反向依赖 `pages/` —— 那会把网络与地图类型都拖进这一层。
 *
 *  `lat/lng` 可选是因为调用方的点可能来自不同来源（信物解出的地点有坐标，
 *  纯文字证据没有）；`flyTo` 的调用方必须自己判空。 */
export interface LocatablePoint {
  id: string;
  name?: string;
  lat?: number;
  lng?: number;
}

/** 证据引用 ref 的解析结果。
 *
 *  后端 `DocumentRef.__str__` 产出 `{source_type}/{source_id}#{chunk_index}`，
 *  例如 `place/p10#0`、`photo/3f2a…#0`。见 `app/schemas/document.py`。 */
export interface ParsedEvidenceRef {
  sourceType: string;
  sourceId: string;
  chunkIndex: number | null;
}

/** 解析证据引用 ref。格式不符时返回 `null` —— **不抛错**：
 *  用户点一条证据不该因为格式意外就崩掉整个面板。 */
export function parseEvidenceRef(ref: string): ParsedEvidenceRef | null {
  if (typeof ref !== 'string') return null;
  const [head, tail] = ref.split('#');
  const slash = head.indexOf('/');
  if (slash <= 0 || slash === head.length - 1) return null;
  const sourceType = head.slice(0, slash);
  const sourceId = head.slice(slash + 1);
  if (!sourceType || !sourceId) return null;
  const chunkIndex = tail && /^\d+$/.test(tail) ? Number(tail) : null;
  return { sourceType, sourceId, chunkIndex };
}

/** 在地图点里找与证据对应的那个。
 *
 * ## 为什么是"尽力而为"而不是保证命中
 *
 * 证据引用里**没有坐标**：`EvidenceBlock` 只有 `index / ref / score / content`
 * （见 `types/agent.ts`），坐标在后端的 `Photo` 表里，两者之间目前没有
 * 一条"source_id → 地图点"的可靠通路（照片建索引的链路还没接）。
 * 所以这里按三种宽松规则试，命中就飞过去，命不中就返回 `null`，
 * 由 UI 明说"这条证据没有坐标"—— 比默默什么都不做要好得多。
 *
 * 匹配优先级：id 全等 → id 以 sourceId 结尾（兼容 `${tripId}:${itemId}` 这类
 * 复合 id）→ name 全等。
 */
export function findPointForEvidence(
  points: readonly LocatablePoint[],
  ref: string,
): LocatablePoint | null {
  const parsed = parseEvidenceRef(ref);
  if (!parsed) return null;

  for (const point of points) {
    if (point.id === parsed.sourceId) return point;
  }
  for (const point of points) {
    if (point.id.endsWith(parsed.sourceId)) return point;
  }
  for (const point of points) {
    if (point.name && point.name === parsed.sourceId) return point;
  }
  return null;
}

/** 地图点去重（同 id 只留第一个）。
 *
 *  照片可能同时挂在多条行程里（后端每次导入都全量重建行程），
 *  同一个 `TripItem.id` 会出现两次。高德对同 id 覆盖物的行为是后画覆盖先画，
 *  视觉上"点少了"，而且这种偏差没有任何报错。 */
export function dedupePoints<T extends { id: string }>(points: readonly T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const point of points) {
    if (seen.has(point.id)) continue;
    seen.add(point.id);
    out.push(point);
  }
  return out;
}
