/** 照片导入客户端（后端 `POST /api/v1/photos/import`）。
 *
 * ## 为什么 EXIF 解析与行程聚类都在后端
 *
 * Web 端 `<input type="file">` 给的 `File` 只有原始字节，前端自己解析 EXIF 需要额外
 * 依赖，而且会和后端（`services/photo_import.py`）解析出两套时间：相册里显示 19:30、
 * 地图上是 20:00 这种错位一旦出现极难定位。所以这里**只负责把字节原样送上去**，
 * `takenAt` / `lat` / `lng` / `semanticTag` 全部以后端回填的为准。
 *
 * ## 为什么失败一律抛 Error
 *
 * 导入是"用户明确点了按钮"的同步流程，静默返回空结果比报错更糟：用户会以为导入成功了。
 * 所以 HTTP 非 2xx、网络异常都照 `agent.ts` / `privacy.ts` 的写法抛成 `Error`，
 * 由调用方决定是弹 toast 还是重试。非 2xx 时**把响应体拼进错误信息**——后端
 * （FastAPI）的具体原因写在 body 里，只留一个 `HTTP 500` 等于逼人去翻后端日志。
 *
 * ## 注入点（单测用）
 *
 * 两种等价方式，优先级从高到低：
 *   1. 调用参数：`importPhotos(files, { baseUrl, fetchImpl })`
 *   2. 模块级默认：`setPhotosApi({ baseUrl, fetchImpl })`，传 `null` 复位
 *   3. 兜底：`import.meta.env.VITE_API_BASE` + `globalThis.fetch`
 *
 * `fetch` 是**每次调用时**从 `globalThis` 上现取的，不是模块加载时快照下来的，
 * 所以 `vi.stubGlobal('fetch', ...)` 能生效。
 */

/** 一张已入库的照片（后端返回的视图）。 */
export interface ImportedPhoto {
  id: string;
  assetId: string;
  hash: string;
  takenAt: string | null;
  takenAtLocal: string | null;
  tzName: string | null;
  lat: number | null;
  lng: number | null;
  coordSystem: string;
  locationSource: string | null;
  locationConfidence: number | null;
  semanticTag: string | null;
  semanticLabel: string | null;
}

/** 后端实际返回的形状（Python 侧 snake_case）。
 *
 * 为什么这份类型要单独声明：JSON 在网络上传输的是 snake_case，而前端的
 * `ImportedPhoto` 是 camelCase。**把两者合成一个接口，就是在骗 TypeScript**
 * —— 编译期说字段叫 `takenAtLocal`，运行时拿到的是 `taken_at_local`，
 * 于是 `photo.takenAtLocal` 恒为 `undefined`，而没人会收到任何报错。
 * 这类 bug 只在"页面上时间栏空白"时才暴露，排查成本极高。
 */
export interface RawImportedPhoto {
  id?: unknown;
  asset_id?: unknown;
  hash?: unknown;
  taken_at?: unknown;
  taken_at_local?: unknown;
  tz_name?: unknown;
  lat?: unknown;
  lng?: unknown;
  coord_system?: unknown;
  location_source?: unknown;
  location_confidence?: unknown;
  semantic_tag?: unknown;
  semantic_label?: unknown;
}

/** 归一化：snake_case → camelCase，类型不对一律折成 null 而不是留下 undefined。 */
export function toImportedPhoto(raw: RawImportedPhoto | null | undefined): ImportedPhoto {
  const source: RawImportedPhoto = raw ?? {};
  return {
    // id / asset_id / hash 后端必定给出，缺失属于协议破损；这里落空串而不是
    // 让 `undefined` 渗进下游（空串在列表里看得见，undefined 只会静默消失）
    id: asString(source.id) ?? '',
    assetId: asString(source.asset_id) ?? '',
    hash: asString(source.hash) ?? '',
    takenAt: asString(source.taken_at),
    takenAtLocal: asString(source.taken_at_local),
    tzName: asString(source.tz_name),
    lat: asNumber(source.lat),
    lng: asNumber(source.lng),
    coordSystem: asString(source.coord_system) ?? '',
    locationSource: asString(source.location_source),
    locationConfidence: asNumber(source.location_confidence),
    semanticTag: asString(source.semantic_tag),
    semanticLabel: asString(source.semantic_label),
  };
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

export interface ImportPhotosResult {
  imported: number;
  duplicates: number;
  failed: number;
  photos: ImportedPhoto[];
  /** @deprecated 后端已不再返回 `trip_ids`（导入不产生行程，见阶段 7 的架构调整）。
   *  字段保留是为了让既有调用点逐个迁移，迁移完成后删除。 */
  tripIds: string[];
}

/** 可注入的客户端配置。留空则回落到环境变量 / 全局 fetch。 */
export interface PhotosApiOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

/** 后端约定的 multipart 字段名。**改这个必须同步改后端 `File(...)` 的参数名**，
 *  否则后端收到 0 个文件，且不会报 422（FastAPI 把空 List 当合法输入）。 */
export const PHOTOS_FIELD = 'files';

/** 错误信息里最多带多少响应体。后端框架的错误页可能几十 KB，全塞进 Error 会把日志撑爆。 */
const MAX_ERROR_BODY = 500;

let override: PhotosApiOptions | null = null;

/** 设置模块级默认配置（`null` = 复位回环境变量 + 全局 fetch）。 */
export function setPhotosApi(options: PhotosApiOptions | null): void {
  override = options;
}

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function excerpt(body: string): string {
  const text = body.trim().replace(/\s+/g, ' ');
  return text.length > MAX_ERROR_BODY ? `${text.slice(0, MAX_ERROR_BODY)}…` : text;
}

function resolveBaseUrl(options?: PhotosApiOptions): string {
  const raw = options?.baseUrl ?? override?.baseUrl ?? import.meta.env.VITE_API_BASE ?? '';
  return raw.replace(/\/+$/, '');
}

function resolveFetch(options?: PhotosApiOptions): typeof fetch {
  const impl = options?.fetchImpl ?? override?.fetchImpl ?? globalThis.fetch;
  if (typeof impl !== 'function') {
    throw new Error('照片导入失败：当前环境没有 fetch，请通过 setPhotosApi 注入 fetchImpl');
  }
  return impl;
}

/** 上传一批照片到后端（后端负责 EXIF 解析 + 行程聚类）。 */
export async function importPhotos(
  files: readonly File[],
  options?: PhotosApiOptions,
): Promise<ImportPhotosResult> {
  if (files.length === 0) {
    throw new Error('请先选择照片');
  }

  const formData = new FormData();
  for (const file of files) {
    // 第三个参数是 filename：后端靠它认扩展名，缺省会落成 `blob` 导致 EXIF 解析跳过
    formData.append(PHOTOS_FIELD, file, file.name);
  }

  const url = `${resolveBaseUrl(options)}/api/v1/photos/import`;
  let response: Response;
  try {
    // Content-Type 必须留给浏览器自己填：multipart 的 boundary 只有运行时才知道
    response = await resolveFetch(options)(url, { method: 'POST', body: formData });
  } catch (err) {
    throw new Error(`照片导入失败：网络请求异常（${describeError(err)}）`);
  }

  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(
      `照片导入失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`,
    );
  }

  // 后端返回 snake_case（Python 侧惯例），前端内部统一 camelCase。
  // 归一化放在**客户端这一层**而不是让调用方各写一遍 `?? ''`，
  // 将来后端改了字段名，只需要动 `toImportedPhoto` 这一处。
  const payload = (await response.json()) as RawImportPhotosResult;
  return {
    imported: asCount(payload.imported),
    duplicates: asCount(payload.duplicates),
    failed: asCount(payload.failed),
    photos: (payload.photos ?? []).map((item) => toImportedPhoto(item as RawImportedPhoto | null)),
    // 后端不再回 trip_ids（导入不聚类）。给个空数组让既有调用点不炸，
    // 但它们不该再读这个值 —— 行程由用户手动组建。
    tripIds: [],
  };
}

/** 批量导入结果的**线格式**（后端实际返回）。 */
export interface RawImportPhotosResult {
  imported?: unknown;
  duplicates?: unknown;
  failed?: unknown;
  photos?: unknown[];
}

function asCount(value: unknown): number {
  const parsed = asNumber(value);
  return parsed === null ? 0 : Math.trunc(parsed);
}


/** 照片库里的一张照片（后端 `GET /api/v1/photos` 的线格式）。
 *
 *  与 `ImportedPhoto` 分开的理由同 `trips.ts` 里的 `RawTripView`：
 *  把 snake_case 与 camelCase 合成一个接口，就是在骗 TypeScript。
 */
export interface RawPhotoListItem {
  id?: unknown;
  hash?: unknown;
  taken_at?: unknown;
  taken_at_local?: unknown;
  tz_name?: unknown;
  lat?: unknown;
  lng?: unknown;
  coord_system?: unknown;
  location_source?: unknown;
  has_location?: unknown;
  location_confidence?: unknown;
  caption?: unknown;
  created_at?: unknown;
}

/** 照片库里的一张照片（前端内部的 camelCase 形态）。 */
export interface PhotoListItem {
  id: string;
  hash: string;
  takenAt: string | null;
  takenAtLocal: string | null;
  tzName: string | null;
  lat: number | null;
  lng: number | null;
  coordSystem: string;
  locationSource: string | null;
  /** 是否已定位。**用后端给的布尔值，不要自己拿 lat 判空**：
   *  只有 lat 没有 lng 是可能的脏数据，判空会让 UI 显示"已定位"却画不出点。 */
  hasLocation: boolean;
  locationConfidence: number | null;
  caption: string | null;
  createdAt: string;
}

/** 线格式 → 前端形态。类型不对一律折成 null/默认值，不放 undefined 渗进下游。 */
export function toPhotoListItem(raw: RawPhotoListItem | null | undefined): PhotoListItem {
  const source: RawPhotoListItem = raw ?? {};
  return {
    id: asString(source.id) ?? '',
    hash: asString(source.hash) ?? '',
    takenAt: asString(source.taken_at),
    takenAtLocal: asString(source.taken_at_local),
    tzName: asString(source.tz_name),
    lat: asNumber(source.lat),
    lng: asNumber(source.lng),
    coordSystem: asString(source.coord_system) ?? 'WGS84',
    locationSource: asString(source.location_source),
    hasLocation: source.has_location === true,
    locationConfidence: asNumber(source.location_confidence),
    caption: asString(source.caption),
    createdAt: asString(source.created_at) ?? '',
  };
}

/**
 * 拉照片库（`GET /api/v1/photos`），按拍摄时刻倒序。
 *
 * ## 为什么空列表是正常结果
 *
 * 还没导入过照片时后端返回 `[]`，那是数据状态不是错误。
 * 调用方（照片库面板）要的是"有几张、哪些没定位"，不是一个异常。
 *
 * ## 为什么 `limit` 有上限
 *
 * 照片库是翻看型界面，一次把几千行连缩略图路径一起送回，
 * 前端渲染和网络都要白等一轮。超上限由后端 422 挡下。
 */
export async function listPhotos(options?: PhotosApiOptions): Promise<PhotoListItem[]> {
  const url = `${resolveBaseUrl(options)}/api/v1/photos`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, { method: 'GET' });
  } catch (err) {
    throw new Error(`获取照片库失败：网络请求异常（${describeError(err)}）`);
  }

  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`获取照片库失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) return [];
  return payload.map((item) => toPhotoListItem(item as RawPhotoListItem | null));
}

/**
 * 给一张照片补标坐标（`PATCH /api/v1/photos/{id}/location`）。
 *
 * ## 为什么坐标范围由后端校验
 *
 * 纬度 ±90、经度 ±180 是地理常识。放一个 999 进来，高德会**静默不渲染**，
 * 用户看到的是"补标了但地图上什么都没有"，而这种失败没有任何报错。
 * 前端也校验（`PhotoLocationDialog`），但两端都验：后端是真相，前端是即时反馈。
 *
 * ## 为什么 422 的错误信息要说人话
 *
 * 后端把 Pydantic 的字段错误折成了一句中文（见 `api/v1/photos.py`），
 * 这里原样带进 `Error.message`，调用方直接弹给用户就能看懂。
 */
export async function patchPhotoLocation(
  photoId: string,
  lat: number,
  lng: number,
  options?: PhotosApiOptions,
): Promise<PhotoListItem> {
  const url = `${resolveBaseUrl(options)}/api/v1/photos/${encodeURIComponent(
    photoId,
  )}/location`;
  let response: Response;
  try {
    response = await resolveFetch(options)(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lng }),
    });
  } catch (err) {
    throw new Error(`坐标补标失败：网络请求异常（${describeError(err)}）`);
  }

  if (response.status === 404) {
    throw new Error('坐标补标失败：照片不存在，或后端尚未启用照片导入能力');
  }
  if (!response.ok) {
    const body = excerpt(await response.text());
    throw new Error(`坐标补标失败：HTTP ${response.status}${body ? ` —— ${body}` : ''}`);
  }

  const payload = (await response.json()) as RawPhotoListItem;
  return toPhotoListItem(payload);
}
