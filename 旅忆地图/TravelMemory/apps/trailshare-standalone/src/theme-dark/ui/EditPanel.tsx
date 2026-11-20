/** 编辑页：标题 / 按天分组的地点 / 每张照片的配文 / 收件人。
 *
 * 阶段 13 / W13-4：深色版复制自 `theme-light/ui/EditPanel`，只删掉"从主应用相册"
 * 那个按钮与它的回调参数（独立版没有主应用相册）。其余排版与文案一字未改。
 *
 * ## 为什么照片在"地点"上加而不是先建一个照片池
 *
 * 协议的结构就是 `天 → 地点 → 照片`，而且每个地点必须 1–5 张。先收照片再让用户
 * 拖拽分组，会把"一个地点几张图"变成拖拽题；而"在地点上加照片"直接把结构对上了。
 *
 * ## 收件人提示（D10）
 *
 * 没填 → 必须显示「未填收件人，这个包只有你自己能打开」。
 * 这句提示是产品承诺，不是可选文案。填了 → 显示"收件人 + 你自己都能打开"。
 */

import type { IntensityBucket, Tag } from '../../types/share';
import { shortPubkey, type LocalIdentity } from '../../core/identity';
import { INTENSITY_OPTIONS, TAG_OPTIONS, totalPhotoBytes, type TripDraft } from '../../core/buildTrip';
import { parseRecipientInput } from '../../core/packFlow';
import { MAX_PHOTOS_PER_PLACE, MIN_PHOTOS_PER_PLACE } from '../../share/schema';

export interface EditPanelProps {
  draft: TripDraft;
  identity: LocalIdentity | null;
  displayName: string;
  recipientsRaw: string;
  busy: boolean;
  error: string | null;
  onTitleChange: (value: string) => void;
  onDisplayNameChange: (value: string) => void;
  onRecipientsChange: (value: string) => void;
  onAddDay: () => void;
  onRemoveDay: (dayIndex: number) => void;
  onDateChange: (dayIndex: number, value: string) => void;
  onAddPlace: (dayIndex: number) => void;
  onRemovePlace: (dayIndex: number, placeIndex: number) => void;
  onPlaceField: (
    dayIndex: number,
    placeIndex: number,
    patch: Partial<{ name: string; label: string; tag: Tag; intensity: IntensityBucket }>,
  ) => void;
  onAddPhotos: (dayIndex: number, placeIndex: number) => void;
  onRemovePhoto: (dayIndex: number, placeIndex: number, photoId: string) => void;
  onCaptionChange: (dayIndex: number, placeIndex: number, photoId: string, value: string) => void;
  onPreview: () => void;
  onBack: () => void;
}

export function EditPanel(props: EditPanelProps): JSX.Element {
  const { draft, identity, busy, error } = props;

  let recipientCount = 0;
  let recipientProblem: string | null = null;
  try {
    recipientCount = parseRecipientInput(props.recipientsRaw).length;
  } catch (caught) {
    recipientProblem = caught instanceof Error ? caught.message : '收件人公钥格式不对';
  }

  const bytes = totalPhotoBytes(draft);
  const photoCount = draft.days.reduce(
    (sum, day) => sum + day.places.reduce((inner, place) => inner + place.photos.length, 0),
    0,
  );

  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h1>填写这段旅程</h1>
          <p className="lead">
            共 {photoCount} 张照片，约 {(bytes / (1024 * 1024)).toFixed(1)} MB 原图（会加密进包）。
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={props.onBack}>
          重新选照片
        </button>
      </header>

      <div className="field-row">
        <label className="field">
          <span>旅程标题</span>
          <input
            type="text"
            value={draft.title}
            placeholder="例：苏州两日"
            onChange={(event) => props.onTitleChange(event.target.value)}
          />
        </label>
        <label className="field">
          <span>署名</span>
          <input
            type="text"
            value={props.displayName}
            placeholder="我"
            onChange={(event) => props.onDisplayNameChange(event.target.value)}
          />
        </label>
      </div>

      {draft.days.map((day, dayIndex) => (
        <section key={day.id} className="day-card">
          <header className="day-head">
            <label className="field field-inline">
              <span>第 {dayIndex + 1} 天</span>
              <input
                type="date"
                value={day.date}
                onChange={(event) => props.onDateChange(dayIndex, event.target.value)}
              />
            </label>
            <div className="day-actions">
              <button type="button" className="btn btn-tiny" onClick={() => props.onAddPlace(dayIndex)}>
                添加地点
              </button>
              {draft.days.length > 1 ? (
                <button
                  type="button"
                  className="btn btn-tiny"
                  onClick={() => props.onRemoveDay(dayIndex)}
                >
                  删除这一天
                </button>
              ) : null}
            </div>
          </header>

          {day.places.map((place, placeIndex) => (
            <article key={place.id} className="place-card">
              <div className="place-head">
                <input
                  className="place-name"
                  type="text"
                  value={place.name}
                  placeholder="地点名（例：拙政园）"
                  onChange={(event) =>
                    props.onPlaceField(dayIndex, placeIndex, { name: event.target.value })
                  }
                />
                <select
                  value={place.tag}
                  aria-label="标签"
                  onChange={(event) =>
                    props.onPlaceField(dayIndex, placeIndex, { tag: event.target.value as Tag })
                  }
                >
                  {TAG_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <select
                  value={place.intensity}
                  aria-label="强度"
                  onChange={(event) =>
                    props.onPlaceField(dayIndex, placeIndex, {
                      intensity: event.target.value as IntensityBucket,
                    })
                  }
                >
                  {INTENSITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.stars} {option.meaning}
                    </option>
                  ))}
                </select>
                <input
                  className="place-label"
                  type="text"
                  value={place.label ?? ''}
                  placeholder="时段（可选）"
                  onChange={(event) =>
                    props.onPlaceField(dayIndex, placeIndex, { label: event.target.value })
                  }
                />
                <button
                  type="button"
                  className="btn btn-tiny"
                  onClick={() => props.onRemovePlace(dayIndex, placeIndex)}
                >
                  删除地点
                </button>
              </div>

              <ul className="photo-list">
                {place.photos.map((photo) => (
                  <li key={photo.id} className="photo-item">
                    <img
                      className="photo-thumb"
                      src={photo.thumbnailDataUrl}
                      alt=""
                      width={56}
                      height={56}
                    />
                    <input
                      className="photo-caption"
                      type="text"
                      value={photo.caption ?? ''}
                      placeholder="配文（可选，会一起加密）"
                      onChange={(event) =>
                        props.onCaptionChange(dayIndex, placeIndex, photo.id, event.target.value)
                      }
                    />
                    <button
                      type="button"
                      className="btn btn-tiny"
                      onClick={() => props.onRemovePhoto(dayIndex, placeIndex, photo.id)}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>

              <div className="place-foot">
                <button
                  type="button"
                  className="btn btn-tiny"
                  disabled={busy || place.photos.length >= MAX_PHOTOS_PER_PLACE}
                  onClick={() => props.onAddPhotos(dayIndex, placeIndex)}
                >
                  + 加照片（{place.photos.length}/{MAX_PHOTOS_PER_PLACE}）
                </button>
                {place.photos.length < MIN_PHOTOS_PER_PLACE ? (
                  <span className="hint hint-warn">每个地点至少要 {MIN_PHOTOS_PER_PLACE} 张照片</span>
                ) : null}
              </div>
            </article>
          ))}
        </section>
      ))}

      <div className="panel-actions">
        <button type="button" className="btn" onClick={props.onAddDay}>
          + 添加一天
        </button>
      </div>

      <section className="recipients">
        <h2>收件人公钥（可选）</h2>
        <p className="hint">
          多个收件人用逗号或空格分隔。带不带 <code>ed25519:</code> 前缀都行。
        </p>
        <textarea
          rows={2}
          value={props.recipientsRaw}
          placeholder="ed25519:3b6a27bc…（留空 = 只有你自己能打开）"
          onChange={(event) => props.onRecipientsChange(event.target.value)}
        />
        {recipientProblem ? (
          <p className="error">{recipientProblem}</p>
        ) : recipientCount === 0 ? (
          <p className="hint hint-strong">未填收件人，这个包只有你自己能打开。</p>
        ) : (
          <p className="hint">
            收件人（{recipientCount} 位）+ 你自己都能打开。你自己：
            <code>ed25519:{identity ? shortPubkey(identity.pubkey) : '……'}…</code>
          </p>
        )}
      </section>

      {error ? <p className="error">{error}</p> : null}

      <footer className="panel-foot">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={props.onPreview}>
          预览
        </button>
      </footer>
    </div>
  );
}
