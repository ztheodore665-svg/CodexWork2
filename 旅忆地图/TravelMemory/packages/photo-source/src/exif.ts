/** EXIF 读取：只取三个字段（拍摄时间 + 两个坐标），其余一概不看。
 *
 * ## 为什么只 pick 三个字段
 *
 * `exifr.parse(bytes)` 会把整个 EXIF 块（含相机型号、序列号、镜头、原图缩略图…）
 * 解析成对象。我们只要拍摄时间和坐标 —— 其余字段进了 JS 堆就等于进了潜在的上报面。
 * `pick` 让 exifr 只解这几个 tag，顺手也快了。
 *
 * ## 时间语义（B14，与 `packages/trail` 的 `Photo.takenAt` 一致）
 *
 * EXIF 的 `DateTimeOriginal` **没有时区**，它是"拍摄地墙钟时间"。
 * 我们按 `Date.UTC(...)` 把它解释成 UTC 存下来 —— 也就是"墙上写 14:05，就存 14:05Z"。
 * 这样：
 * - 同一天的照片分组（`YYYY-MM-DD`）永远等于用户在相机里看到的日期；
 * - 不需要知道拍摄地时区（EXIF 里反正没有）；
 * - 与主应用已有的 `Photo.takenAt` 语义完全一致，导入时不会差 8 小时。
 *
 * 代价：这个时间不是真正的物理时刻。这是刻意的取舍，主应用早就这么做。
 *
 * ## 失败一律返回 `{}`
 *
 * 没有 EXIF 是常态（截图、微信保存的图、被洗过的图）。抛错会让整批导入失败，
 * 而"读不到就不填 takenAt/lat/lng"是完全可以接受的降级。
 */

export interface ExifMeta {
  /** ISO UTC。语义见文件头注释。 */
  takenAt?: string;
  /** WGS84。 */
  lat?: number;
  /** WGS84。 */
  lng?: number;
}

/** exifr 的最小形状。只声明用到的方法，避免把包的公共类型绑在 exifr 的 d.ts 上。 */
interface ExifrLike {
  parse(
    input: ArrayBuffer,
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown> | undefined>;
}

/** 真正要解的 tag。`GPS*Ref` 不是数值而是 'N'/'S'/'E'/'W'，缺了它南半球的坐标会翻到北半球。 */
const PICKED_TAGS = [
  'DateTimeOriginal',
  'GPSLatitude',
  'GPSLongitude',
  'GPSLatitudeRef',
  'GPSLongitudeRef',
] as const;

/** 延迟到第一次真解析时才加载 exifr：没照片的页面不付这个体积。 */
async function loadExifr(): Promise<ExifrLike | null> {
  try {
    const module_ = (await import('exifr')) as unknown as {
      default?: ExifrLike;
      parse?: unknown;
    };
    const candidate = module_.default ?? (module_ as unknown as ExifrLike);
    return typeof candidate?.parse === 'function' ? candidate : null;
  } catch {
    return null;
  }
}

/** `'30/1'` 这类 EXIF 有理数字符串 → 数字。
 *
 * 也接受 `[分子, 分母]` 数组形态：exifr 在 `translateValues: false` 下可能把有理数
 * 原样交回来（`[[30,1],[15,1],[3240,100]]`），少了这一支南半球的坐标会直接丢掉。
 */
function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) {
    const numerator = toFiniteNumber(value[0]);
    if (numerator === undefined) return undefined;
    const denominator = value.length > 1 ? toFiniteNumber(value[1]) : 1;
    if (denominator === undefined || denominator === 0) return undefined;
    return numerator / denominator;
  }
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  const slash = trimmed.indexOf('/');
  if (slash > 0) {
    const numerator = Number(trimmed.slice(0, slash));
    const denominator = Number(trimmed.slice(slash + 1));
    if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) {
      return undefined;
    }
    return numerator / denominator;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** 用 `Date.UTC` 组装 ISO 字符串；月/日/时越界返回 `undefined`。 */
function isoFromParts(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
): string | undefined {
  if (
    !Number.isInteger(year) ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > 31 ||
    hour < 0 ||
    hour > 23 ||
    minute < 0 ||
    minute > 59 ||
    second < 0 ||
    second > 60
  ) {
    return undefined;
  }
  const ms = Date.UTC(year, month - 1, day, hour, minute, Math.min(second, 59));
  if (!Number.isFinite(ms)) return undefined;
  const date = new Date(ms);
  // 2 月 30 日这种非法日期会被 Date.UTC 顺延到 3 月，回读后月份变了就说明输入非法。
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return undefined;
  return date.toISOString();
}

/** `DateTimeOriginal` → ISO UTC。兼容字符串 / 数组 / Date（视 exifr 版本与选项而定）。
 *
 * `@internal` 导出仅供单测：EXIF 里 `DateTimeOriginal` 的形态太多（字符串、Date、
 * `[y,m,d,h,mi,s]` 数组），每种都要能正确处理，而这几个分支用真实 JPEG 覆盖成本高。
 */
export function toIsoUtc(value: unknown): string | undefined {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? undefined : value.toISOString();
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    const match = /^(\d{4})[:\-/](\d{1,2})[:\-/](\d{1,2})[ T](\d{1,2}):(\d{1,2}):(\d{1,2})/.exec(trimmed);
    if (match) {
      return isoFromParts(
        Number(match[1]),
        Number(match[2]),
        Number(match[3]),
        Number(match[4]),
        Number(match[5]),
        Number(match[6]),
      );
    }
    const parsed = Date.parse(trimmed);
    return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString();
  }
  if (Array.isArray(value)) {
    const parts = value.map((part) => toFiniteNumber(part));
    if (parts.length >= 3 && parts.slice(0, 3).every((part) => part !== undefined)) {
      return isoFromParts(
        parts[0] as number,
        parts[1] as number,
        parts[2] as number,
        parts[3] ?? 0,
        parts[4] ?? 0,
        Math.round(parts[5] ?? 0),
      );
    }
  }
  return undefined;
}

/** `GPSLatitude`（度分秒数组或已转好的十进制）→ 十进制角度，并按 Ref 修正符号。
 *
 * `@internal` 导出仅供单测：南/西半球的符号修正写错时，地图上会把照片放到对跖点，
 * 这种 bug 在真机上极难发现，必须单测锁住。
 */
export function toDecimalDegrees(value: unknown, ref: unknown): number | undefined {
  let decimal: number | undefined;
  if (Array.isArray(value)) {
    const parts = value.map((part) => toFiniteNumber(part));
    if (parts.length < 2 || parts[0] === undefined) return undefined;
    const degrees = parts[0] as number;
    const minutes = parts[1] ?? 0;
    const seconds = parts[2] ?? 0;
    decimal = Math.abs(degrees) + minutes / 60 + seconds / 3600;
  } else {
    const single = toFiniteNumber(value);
    if (single === undefined) return undefined;
    decimal = Math.abs(single);
  }
  if (!Number.isFinite(decimal)) return undefined;
  const upperRef = typeof ref === 'string' ? ref.trim().toUpperCase() : '';
  return upperRef === 'S' || upperRef === 'W' ? -decimal : decimal;
}

function isLatitude(value: number | undefined): value is number {
  return value !== undefined && value >= -90 && value <= 90;
}

function isLongitude(value: number | undefined): value is number {
  return value !== undefined && value >= -180 && value <= 180;
}

/**
 * 从图片字节里读拍摄时间与 GPS 坐标。
 *
 * @param bytes 原始图片字节（JPEG 的 EXIF / HEIC 的 metadata 都能读；PNG 一般没有）
 * @returns 读到的字段；任何失败（没有 EXIF、exifr 加载失败、字段越界）都返回 `{}`，绝不抛错。
 */
export async function readExifMeta(bytes: ArrayBuffer): Promise<ExifMeta> {
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 4) return {};
  const exifr = await loadExifr();
  if (!exifr) return {};

  let parsed: Record<string, unknown> | undefined;
  try {
    parsed = await exifr.parse(bytes, {
      pick: [...PICKED_TAGS],
      // 关掉值翻译：让 DateTimeOriginal 保持 'YYYY:MM:DD HH:MM:SS' 原文，
      // 我们自己在 isoFromParts 里用 Date.UTC 解释它，语义可控且不依赖 exifr 的本地时区推断。
      translateValues: false,
      reviveValues: false,
    });
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== 'object') return {};

  const meta: ExifMeta = {};
  const takenAt = toIsoUtc(parsed.DateTimeOriginal);
  if (takenAt !== undefined) meta.takenAt = takenAt;

  const lat = toDecimalDegrees(parsed.GPSLatitude, parsed.GPSLatitudeRef);
  const lng = toDecimalDegrees(parsed.GPSLongitude, parsed.GPSLongitudeRef);
  // 只有经纬度都成立才一起写：半对坐标画不出点，下游会遇到"有 lat 没 lng"的脏数据。
  if (isLatitude(lat) && isLongitude(lng)) {
    meta.lat = lat;
    meta.lng = lng;
  }
  return meta;
}
