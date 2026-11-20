/** 端能力（相册 / 缩略图缓存 / 权限）契约。
 *
 * ## 为什么要有这一层
 *
 * 「旅忆」的核心交互是让用户从相册里挑照片，再按时间+地点缝进地图。而"读相册"
 * 这件事在 Web、iOS、Android 上是三套完全不同的 API：
 *
 *   - Web：`<input type="file">` + `createImageBitmap`，拿不到 EXIF/GPS
 *   - iOS：`PhotoKit` / `UIImagePickerController`，必须有 NSPhotoLibraryUsageDescription
 *   - Android：`Photo Picker`（Android 13+ 无需权限）/ `READ_MEDIA_IMAGES`
 *
 * 如果业务代码直接调用平台 API，接真机那天就要把每个页面翻一遍。所以这里先把
 * **接缝**划出来：上层只认 `NativeBridge`，具体怎么实现由 `services/native/` 决定。
 *
 * ## 三个刻意的设计取舍
 *
 * 1. **`PhotoAsset.takenAt` 与 `takenAtLocal` 分开存**。
 *    照片的拍摄时间是"拍摄地当地时间"，但越靠近极端的时区转换越容易出错
 *    （尤其跨时区的长途旅行）。与其在端侧猜，不如两个都留：UTC 用于索引排序，
 *    当地时间用于"这张照片是傍晚拍的"这类语义判断。
 * 2. **`coordSystem` 必须显式标注**。高德地图用 GCJ-02，EXIF 里写的是 WGS84，
 *    混用会偏移几百米。宁可多一个字段，也不要让下游默认。
 * 3. **`readThumb` 返回 `ArrayBuffer` 而不是 `Blob` / `dataURL`**。
 *    ArrayBuffer 可以原样塞进 IndexedDB 和 RPC body，dataURL 会多 33% 体积还给转义添麻烦。
 */

export type NativePlatform = 'web' | 'ios' | 'android';

export interface PhotoAsset {
  id: string; // 平台侧稳定标识
  width: number;
  height: number;
  takenAt?: string; // ISO，UTC
  takenAtLocal?: string; // ISO，无时区，拍摄地当地时间
  lat?: number;
  lng?: number;
  coordSystem?: 'WGS84' | 'GCJ-02';
  locationSource?: 'exif' | 'inferred' | 'user' | 'none';
}

export interface PickPhotosOptions {
  limit?: number; // 最多选几张
}

export interface PhotoSource {
  readonly platform: NativePlatform;
  pickPhotos(options?: PickPhotosOptions): Promise<PhotoAsset[]>;
  readThumb(assetId: string, maxEdge?: number): Promise<ArrayBuffer>;
  /** 把**页面上已有的** `File[]` 交给相册层，同步换回同序的 `PhotoAsset[]`。
   *
   * 为什么需要它：`pickPhotos()` 的语义是"弹系统选择器"，而页面上「导入照片」按钮
   * 要的是"我自己有一个隐藏的 `<input type=file>`，用户选完把文件直接喂进来"。
   * 强行用 `pickPhotos` 会弹两次选择器。
   *
   * 之所以是**可选**方法：原生端没有 `File` 这个概念（PhotoKit 给的是 `PHAsset`），
   * 它的 `adoptFiles` 只能抛"未接入"。调用方必须自己判空。
   */
  adoptFiles?(files: readonly File[]): PhotoAsset[];
}

export type PermissionName = 'photos' | 'camera' | 'location';
export type PermissionState = 'granted' | 'denied' | 'prompt' | 'unsupported';

export interface Permissions {
  query(name: PermissionName): Promise<PermissionState>;
  request(name: PermissionName): Promise<PermissionState>;
}

export interface ThumbnailStore {
  get(assetId: string, maxEdge: number): Promise<ArrayBuffer | null>;
  put(assetId: string, maxEdge: number, bytes: ArrayBuffer): Promise<void>;
  clear(): Promise<void>;
  readonly sizeHint: number; // 当前缓存条目数（近似）
}

export interface NativeBridge {
  readonly platform: NativePlatform;
  readonly photos: PhotoSource;
  readonly permissions: Permissions;
  readonly thumbs: ThumbnailStore;
}
