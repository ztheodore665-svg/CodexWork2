/** `@travelmemory/photo-source` 对外出口。
 *
 * 只做 re-export：包的边界就是这五个模块，加逻辑请放回对应文件。
 */

export { MAX_THUMBNAIL_BYTES, type PhotoSource, type PickOptions, type SystemPhoto } from './types';

export {
  FALLBACK_THUMBNAIL_DATA_URL,
  THUMBNAIL_MAX_EDGE,
  THUMBNAIL_MIN_QUALITY,
  THUMBNAIL_QUALITY_STEP,
  THUMBNAIL_START_QUALITY,
  compressToThumbnail,
  dataUrlByteLength,
  getSourceSize,
  planThumbnailSize,
  type Canvas2DContextLike,
  type CanvasLike,
  type ThumbnailDeps,
  type ThumbnailSourceLike,
} from './thumbnail';

export { readExifMeta, type ExifMeta } from './exif';

export {
  createRuntimePhotoId,
  createSystemSource,
  openPicker,
  pickFromSystem,
  readPhotoFile,
  type PhotoFileDeps,
  type PickSystemDeps,
} from './system-picker';

export {
  createLibrarySource,
  getInjectedPhotos,
  isEmbedded,
  resetInjectedPhotos,
  setInjectedPhotos,
} from './iframe-bridge';
