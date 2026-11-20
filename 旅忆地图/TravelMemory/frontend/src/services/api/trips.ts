/** 行程列表客户端（后端 `GET /api/v1/trips`）。
 *
 * ## 为什么 trip 是后端聚类出来的，前端只读
 *
 * `services/trip_cluster.py` 按「时间相邻 + 空间相邻」单遍扫描切分行程，这套规则要
 * 读全部照片的 `taken_at` 与坐标。前端手上只有当次导入的几张，拿一半数据聚类就会
 * 出现"同一趟旅行被切成三段"。所以前端只消费结果，`source` 字段（cluster / manual）
 * 标出这条行程是算出来的还是用户手改的——手改过的下次不能被聚类覆盖掉。
 *
 * ## 错误处理与注入点
 *
 * 与 `photos.ts` 完全同构：非 2xx 抛 Error 并带上响应体；支持
 * `listTrips({ baseUrl, fetchImpl })` 与 `setTripsApi({ ... })`（传 `null` 复位）两级注入，
 * 兜底 `import.meta.env.VITE_API_BASE` + `globalThis.fetch`。
 */

export interface TripItemView {
  id: string;
  photoId: string;
  takenAt: string | null;
  lat: number | null;
  lng: number | null;
  semanticTag: string | null;
  semanticLabel: string | null;
}

export interface TripView {
  id: string;
  title: string;
  startedAt: string | null;
  endedAt: string | null;
  /** "cluster"（自动聚类）| "manual"（用户手改过，后续重算不覆盖）。 */
  source: string;
  itemCount: number;
  items: TripItemView[];
}

export interface TripListResponse {
  trips: TripView[];
}

/** 线格式（后端 snake_case）。与 `ImportedPhoto` 分离的理由同上。 */
export interface RawTripItemView {
  id?: unknown;
  photo_id?: unknown;
  taken_at?: unknown;
  lat?: unknown;
  lng?: unknown;
  semantic_tag?: unknown;
  semantic_label?: unknown;
}

export interface RawTripView {
  id?: unknown;
  title?: unknown;
  started_at?: unknown;
  ended_at?: unknown;
  source?: unknown;
  item_count?: unknown;
  items?: unknown;
}

export interface RawTripListResponse {
  trips?: unknown;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function toTripItemView(raw: RawTripItemView | null | undefined): TripItemView {
  const source: RawTripItemView = raw ?? {};
  return {
    id: asString(source.id) ?? '',
    photoId: asString(source.photo_id) ?? '',
    takenAt: asString(source.taken_at),
    lat: asNumber(source.lat),
    lng: asNumber(source.lng),
    semanticTag: asString(source.semantic_tag),
    semanticLabel: asString(source.semantic_label),
  };
}

export function toTripView(raw: RawTripView | null | undefined): TripView {
  const source: RawTripView = raw ?? {};
  const items = asArray(source.items).map((item) =>
    toTripItemView(item as RawTripItemView | null),
  );
  return {
    id: asString(source.id) ?? '',
    title: asString(source.title) ?? '',
    startedAt: asString(source.started_at),
    endedAt: asString(source.ended_at),
    source: asString(source.source) ?? 'cluster',
    itemCount: asNumber(source.item_count) ?? items.length,
    items,
  };
}

/** 可注入的客户端配置。留空则回落到环境变量 / 全局 fetch。 */
export interface TripsApiOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

/** 建行程的选项：比通用选项多一个 `startedOn`（来自 journey 包的 `days[0].date`）。 */
export interface CreateTripOptions extends TripsApiOptions {
  startedOn?: string;
}

/** 错误信息里最多带多少响应体（同 `photos.ts`，防止错误页撑爆日志）。 */
const MAX_ERROR_BODY = 500;

let override: TripsApiOptions | null = null;

/** 设置模块级默认配置（`null` = 复位回环境变量 + 全局 fetch）。 */
export function setTripsApi(options: TripsApiOptions | null): void {
  override = options;
}

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function excerpt(body: string): string {
  const text = body.trim().replace(/\s+/g, ' ');
  return text.length > MAX_ERROR_BODY ? `${text.slice(0, MAX_ERROR_BODY)}…` : text;
}

function resolveBaseUrl(options?: TripsApiOptions): string {
  const raw = options?.baseUrl ?? override?.baseUrl ?? import.meta.env.VITE_API_BASE ?? '';
  return raw.replace(/\/+$/, '');
}

/**
 * 写请求（POST / PATCH）统一用的 JSON 请求头。
 *
 * ## 为什么收敛成一个函数
 *
 * 三处调用（`createTrip` / `renameTrip` / `assignTripPhotos`）都是
 * `JSON.stringify` 出来的 body，头必须逐字节一致 —— 复制粘贴三份的代价是
 * 哪天要给写请求加一个头（例如幂等键），漏改一处的表现是「接口偶发 415」，
 * 而单测里 mock 的 fetch 往往不校验头，测不出来。
 *
 * 这里**不**拼任何鉴权头：后端鉴权走 cookie / 同源代理，
 * 前端从来没有 `Authorization`，不要顺手加上。
 */
function jsonHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json' };
}

function resolveFetch(options?: TripsApiOptions): typeof fetch {
  const impl = options?.fetchImpl ?? override?.fetchImpl ?? globalThis.fetch;
  if (typeof impl !== 'function') {
    throw new Error('获取行程列表失败：当前环境没有 fetch，请通过 setTripsApi 注入 fetchImpl');
  }
  return impl;
}

/** 拉取当前用户的全部行程。空列表是正常结果（还没导入过照片），不抛错。 */
export async function listTrips(options?: TripsApiOptions): Promise<TripView[]> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, { method: 'GET' });
  } catch (err) {
    throw new Error(`获取行程列表失败：网络请求异常（${describeError(err)}）`);
  }

  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`获取行程列表失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  // 后端返回 snake_case（`started_at` / `item_count` / `trip_ids`），
  // 前端内部一律 camelCase —— 归一化只在客户端这一层做一次。
  const payload = (await response.json()) as Partial<RawTripListResponse>;
  return asArray(payload.trips).map((item) => toTripView(item as RawTripView | null));
}

/** `POST /api/v1/trips` 的响应体（后端 `TripCreateResponse`）。 */
export interface RawTripCreateResponse {
  trip?: unknown;
}

/**
 * 手工创建一个**空**行程，返回后端给的那一条（`source = 'manual'`）。
 *
 * ## 为什么这里不自己拼一个 TripView 返回
 *
 * 后端要生成的只有 id 和主键，但"这次到底建成了什么"只有后端知道：
 * `source` 是不是真的 `manual`、`started_at` 是不是 `null`、标题被剥成什么样。
 * 前端按入参自己拼一份，等于把"创建成功"变成一个**本地断言** ——
 * 哪天后端改了降级策略（比如开关没开时返回 404），前端照样报"创建成功"。
 *
 * ## 404 的错误信息为什么要说人话
 *
 * 后端 `photo_import` 开关关着时返回 404（能力不存在，不是权限问题）。
 * 直接把 `HTTP 404` 抛给上层，用户看到的是一句看不懂的数字；
 * 而这是**可预期的运营状态**，不是 bug —— 所以在这里翻译成一句能行动的话。
 */
export async function createTrip(
  title: string,
  options?: CreateTripOptions,
): Promise<TripView> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, {
      method: 'POST',
      headers: jsonHeaders(),
      // `started_on` 只在导入 journey 包时出现：它是包里的 `days[0].date`，
      // 不是用户手填的。手工新建传 undefined，后端按 null 处理（"还不知道"）。
      body: JSON.stringify(
        options?.startedOn ? { title, started_on: options.startedOn } : { title },
      ),
    });
  } catch (err) {
    throw new Error(`新建旅程失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('新建旅程失败：后端尚未启用照片导入能力（photo_import 开关未开）');
  }

  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`新建旅程失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  const payload = (await response.json()) as Partial<RawTripCreateResponse>;
  return toTripView(payload.trip as RawTripView | null);
}

/**
 * 重命名一趟行程（`PATCH /api/v1/trips/{id}`）。
 *
 * ## 为什么返回刷新后的那一条，而不是把入参回显
 *
 * 后端会把 `source` 从 `cluster` 提升成 `manual`（改名的行程从此退出自动重建，
 * 否则下次导入会把用户敲的标题冲掉）。这个副作用**只有后端知道** ——
 * 前端按入参拼一个返回值，就会在界面上继续显示"聚类"徽章，
 * 而下一次导入后标题变了，用户完全看不出为什么。
 *
 * 404 的错误信息与 `createTrip` 同样翻译成人话：行程不存在与能力没开
 * 都走这个码，而后者是**可预期的运营状态**。
 */
export async function renameTrip(
  id: string,
  title: string,
  options?: TripsApiOptions,
): Promise<TripView> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips/${encodeURIComponent(id)}`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, {
      method: 'PATCH',
      headers: jsonHeaders(),
      body: JSON.stringify({ title }),
    });
  } catch (err) {
    throw new Error(`重命名失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('重命名失败：该行程不存在，或后端尚未启用照片导入能力');
  }
  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`重命名失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  const payload = (await response.json()) as Partial<RawTripCreateResponse>;
  return toTripView(payload.trip as RawTripView | null);
}

/**
 * 删除一趟行程（`DELETE /api/v1/trips/{id}`）。**照片一条不少。**
 *
 * ## 为什么前端不需要做任何本地清理
 *
 * 后端只删 `Trip` 与 `TripItem`（外键 CASCADE 带走关联），`photo` 表
 * 从头到尾没有写操作。所以删完之后照片库与地图上的照片点都还在 ——
 * 前端只要把这一趟从列表里去掉，别的什么都不用动。
 *
 * 这也是为什么这个函数**不返回照片列表**：调用方要知道"照片还在"，
 * 重新拉一次 `GET /photos` 就是了，让删除响应兼作数据源只会把两件事耦在一起。
 */
export async function deleteTrip(id: string, options?: TripsApiOptions): Promise<void> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips/${encodeURIComponent(id)}`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, { method: 'DELETE' });
  } catch (err) {
    throw new Error(`删除行程失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('删除行程失败：该行程不存在，或后端尚未启用照片导入能力');
  }
  // 204 是成功：没有响应体要读。但非 2xx 仍要抛，否则"删掉了"是个本地幻觉
  if (!response.ok && response.status !== 204) {
    const body = excerpt(await response.text());
    throw new Error(`删除行程失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }
}

/**
 * 把一批照片设成行程的目标快照（`POST /api/v1/trips/{id}/photos`）。
 *
 * ## 为什么是"全量快照"而不是"加这几张"
 *
 * 前端持有这个行程的完整照片列表，由它 diff 出该加谁该删谁。
 * 后端猜增量的代价是幽灵条目（"我以为加了、其实没加"），
 * 而这种不一致在界面上没有任何异常表现。详见服务层 docstring。
 *
 * ## 为什么顺序重要
 *
 * `ord` 就是传入顺序：用户想让哪张排第一，后端就照办。
 * 阶段 7 之前行程是聚类产物、按时间排；现在是用户手动组建的视图，
 * 再按时间重排一次等于把用户的排列意图悄悄改掉。
 */
export async function assignTripPhotos(
  tripId: string,
  photoIds: readonly string[],
  options?: TripsApiOptions,
): Promise<TripView> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips/${encodeURIComponent(tripId)}/photos`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ photo_ids: photoIds }),
    });
  } catch (err) {
    throw new Error(`保存行程失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('保存行程失败：该行程或某张照片不存在');
  }
  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`保存行程失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  const payload = (await response.json()) as Partial<RawTripCreateResponse>;
  return toTripView(payload.trip as RawTripView | null);
}

/**
 * 把一张照片从行程里移除（`DELETE /api/v1/trips/{id}/photos/{photoId}`）。
 * **只解除关联，照片本身留在库里。**
 *
 * 后端对"照片本就不在这趟"也返回 204：调用方想让它不在，结果确实是不在。
 */
export async function removeTripPhoto(
  tripId: string,
  photoId: string,
  options?: TripsApiOptions,
): Promise<void> {
  const url = `${resolveBaseUrl(options)}/api/v1/trips/${encodeURIComponent(
    tripId,
  )}/photos/${encodeURIComponent(photoId)}`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, { method: 'DELETE' });
  } catch (err) {
    throw new Error(`移除照片失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('移除照片失败：该行程不存在');
  }
  if (!response.ok && response.status !== 204) {
    const body = excerpt(await response.text());
    throw new Error(`移除照片失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }
}
