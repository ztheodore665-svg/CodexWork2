/** 导入卡片：本地读文件 → 解包 → 验签报告 + 时间线 → 交给地图画模糊圈。
 *
 * ## 为什么文件输入要藏起来
 *
 * 原生 `<input type="file">` 的样式各浏览器不一致，且宽度、语言、文案都不受控，
 * 放在"端到端加密"这种需要建立信任的页面上很掉价。这里用一个可控的虚线容器
 * （`<label htmlFor>`）代理点击，**底层仍然是原生 file input** ——
 * 拖拽/可选文件夹等自绘方案在非 Chromium 上行为参差，不值得为一个视觉收益冒险。
 *
 * ## 为什么三个校验态用 Badge 而不是 ✅/❌
 *
 * 签名 / 承诺 / 哈希链各自都可能三态（通过 / 失败 / 未比对）。emoji 在 Windows 与各字体下
 * 渲染不一致，"⬜vs✅"的语义差别还容易看错；Badge 有统一色板，
 * 且"未比对"（灰）必须和"失败"（红）在视觉上距离足够远 —— 那是两种完全不同的结论。
 */

import { Loader2, MapPinned, Upload } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '../../components/ui/alert';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../../components/ui/card';
import { Separator } from '../../components/ui/separator';
import {
  INTENSITY_MEANING,
  INTENSITY_STARS,
  TAG_LABELS,
  type UnpackedPlace,
  type UnpackViewerResult,
} from '../../types/share';

export interface ImportCardProps {
  importing: boolean;
  error: string | null;
  /** 最近一次导入的结果（`null` = 还没导入 / 已清空）。 */
  view: UnpackViewerResult | null;
  onFile: (file: File) => void;
  onViewOnMap?: (places: UnpackedPlace[]) => void;
}

export function ImportCard({ importing, error, view, onFile, onViewOnMap }: ImportCardProps) {
  const places: UnpackedPlace[] =
    view && view.ok ? view.groups.flatMap((group) => group.places) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Upload className="h-5 w-5 text-primary" />
          导入信物
        </CardTitle>
        <CardDescription>
          选择别人发给你的 <code className="font-mono text-xs">.trailshare</code> 文件，在本机用你的私钥解开。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <label
          htmlFor="share-file-input"
          className="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-border bg-muted/30 px-6 py-8 text-center transition-colors hover:border-primary/60 hover:bg-accent/60"
        >
          <Upload className="mb-1 h-6 w-6 text-muted-foreground" />
          <span className="text-sm font-medium">点击选择信物文件</span>
          <span className="text-xs text-muted-foreground">文件只在本机解析，不会上传任何内容</span>
          <input
            id="share-file-input"
            type="file"
            accept=".trailshare,application/json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
              // 清空 value：连续导入同一个文件时也触发 change
              event.target.value = '';
            }}
          />
        </label>

        {importing && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在本机解包…
          </p>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertTitle>无法读取这个信物</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {view && !view.ok && (
          <Alert variant="destructive">
            <AlertTitle>解不开</AlertTitle>
            <AlertDescription>{view.reason}</AlertDescription>
          </Alert>
        )}

        {view && view.ok && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">
                来自 <strong className="font-semibold">{view.from.displayName}</strong>
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {view.from.pubkey.slice(0, 24)}…
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <VerifyBadge label="验签" ok={view.report.signatureOk} />
              <VerifyBadge label="承诺" ok={view.report.commitmentOk} />
              <VerifyBadge label="哈希链" ok={view.chainOk} />
            </div>

            <Separator />

            <div className="space-y-3">
              {view.groups.map((group) =>
                group.places.map((place) => (
                  <div key={`${group.groupId}-${place.placeId}`} className="rounded-lg border bg-muted/30 p-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div className="text-sm font-medium">
                        {place.label ? <span className="text-muted-foreground">{place.label} · </span> : null}
                        {place.name}
                      </div>
                      <div className="text-xs text-muted-foreground">{place.date}</div>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                      <Badge variant="secondary">{TAG_LABELS[place.tag]}</Badge>
                      <Badge variant="outline">
                        {INTENSITY_STARS[place.intensity]} {INTENSITY_MEANING[place.intensity]}
                      </Badge>
                      <span className="text-muted-foreground">{place.photos.length} 张照片</span>
                    </div>
                    {typeof place.radius === 'number' && place.radius > 0 ? (
                      <p className="mt-2 text-xs text-emerald-700">
                        模糊圈：半径约 {place.radius} 米（真实位置只在圈内某处）
                      </p>
                    ) : null}
                  </div>
                )),
              )}
            </div>

            {places.length > 0 && onViewOnMap && (
              <Button variant="outline" onClick={() => onViewOnMap(places)}>
                <MapPinned className="h-4 w-4" />
                在地图上查看
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** 单个校验态。`null` = 无法比对（缺字段 / 链未启用），语义上**不等于失败**。 */
function VerifyBadge({ label, ok }: { label: string; ok: boolean | null }) {
  const variant = ok === null ? 'muted' : ok ? 'secure' : 'destructive';
  const text = ok === null ? '未比对' : ok ? '通过' : '失败';
  return (
    <Badge variant={variant}>
      {label}：{text}
    </Badge>
  );
}
