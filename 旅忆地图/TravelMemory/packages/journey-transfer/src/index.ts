/** `@travelmemory/journey-transfer` 对外出口。
 *
 * 只做 re-export：包的边界就是这三个模块，加逻辑请放回 `types.ts` / `export.ts` / `import.ts`。
 */

export {
  INTENSITY_VALUES,
  JOURNEY_BUNDLE_VERSION,
  MAX_THUMBNAIL_BYTES,
  TAG_VALUES,
  type JourneyBundle,
  type JourneyDay,
  type JourneyIntensity,
  type JourneyPhoto,
  type JourneyPhotoLike,
  type JourneyPlace,
  type JourneyTag,
} from './types';

export {
  FALLBACK_TITLE,
  asNonEmptyString,
  asOptionalFiniteNumber,
  asOptionalString,
  asRecord,
  base64ByteLength,
  bundleFromTrip,
  isDataUrl,
  isJourneyIntensity,
  isJourneyTag,
  mapBundlePhoto,
  normalizeDays,
  normalizePlaces,
  type BundleFromTripOptions,
  type JourneyPhotoLookup,
  type PhotoMapper,
} from './export';

export { tripFromBundle, type ImportedJourney } from './import';
