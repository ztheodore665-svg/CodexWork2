/** 验证信物卡片（阶段 7 W7-3）：拖一个 `.trailshare` 进来，在本机验签 / 比对承诺 / 判链。
 *
 * ## 和 ImportCard 的分工
 *
 * `ImportCard` 关心"**里面写了什么**"（解出来的时间线与地点）；
 * 这张卡片只关心"**能不能信**"（三条可验证断言）。两者共用同一个 `unpackForViewer()`，
 * 所以看到的结论必然一致 —— 不存在"导入说通过、验证说不通过"的分叉。
 *
 * ## 视觉语言：颜色只表达"证据强度"
 *
 * 绿（pass）= 本机找到了证实证据；红（fail）= 本机找到了证伪证据；
 * 黄灰（unknown）= 本机**没有**证据（缺字段 / 跨设备链）。
 * 三种颜色的区别是密码学语义，不是装饰 —— 具体理由见 `verification.ts` 的文件头注释。
 *
 * ## 红线
 *
 * - 不出现任何"总数暗示"（共 N 组 / N 天 / N 个地点）：`UnpackView` 刻意不暴露组数，
 *   这里也不渲染任何能反推组数的东西；
 * - 全程无网络：`unpackForViewer` 只吃本地字节，底部 Alert 明确写出这一点。
 */

import { useCallback, useRef, useState } from 'react';
import { CircleAlert, CircleCheck, CircleX, Loader2, ShieldCheck, Upload } from 'lucide-react';

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
import { unpackForViewer } from '../../services/share/unpack';
import type { UnpackCredential, UnpackViewerResult } from '../../types/share';
import { CHECK_STATE_LABELS, buildVerificationView, type CheckItem, type CheckState } from './verification';

export interface VerifyCardProps {
  /** 本机凭证（种子）。只用来解包与验签，不离开这台设备。 */
  cred: UnpackCredential;
  /** 外部注入的解包结果（例如沿用导入区的结论）。非空时优先于本卡片自己解析出的结果。 */
  externalResult?: UnpackViewerResult | null;
  onVerified?: (result: UnpackViewerResult) => void;
}

const STATE_ICON: Record<CheckState, typeof CircleCheck> = {
  pass: CircleCheck,
  fail: CircleX,
  unknown: CircleAlert,
};

const STATE_ICON_COLOR: Record<CheckState, string> = {
  pass: 'text-emerald-600',
  fail: 'text-destructive',
  unknown: 'text-amber-600',
};

const STATE_BADGE_VARIANT: Record<CheckState, 'secure' | 'destructive' | 'muted'> = {
  pass: 'secure',
  fail: 'destructive',
  unknown: 'muted',
};

export function VerifyCard({ cred, externalResult, onVerified }: VerifyCardProps) {
  const [local, setLocal] = useState<UnpackViewerResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  /** 本卡片自己解析出的结果优先；没有时沿用导入区的结论（同一个包，结论必然一致）。 */
  const result = local ?? externalResult ?? null;
  const verification = buildVerificationView(result);

  const verifyFile = useCallback(
    async (file: File) => {
      setVerifying(true);
      setReadError(null);
      setLocal(null);
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as unknown;
        const next = await unpackForViewer(parsed, cred);
        setLocal(next);
        onVerified?.(next);
      } catch (exc) {
        // 读不出文本 / 不是 JSON：这两种都只是"这个文件没法验证"，不是"验证失败"
        setReadError((exc as Error).message);
      } finally {
        setVerifying(false);
      }
    },
    [cred, onVerified],
  );

  const hasFail = verification.items.some((item) => item.state === 'fail');
  const hasUnknown = verification.items.some((item) => item.state === 'unknown');
  const allPass = verification.items.every((item) => item.state === 'pass');

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-5 w-5 text-emerald-600" />
          验证信物
          <Badge variant="secure">本机验证</Badge>
        </CardTitle>
        <CardDescription>
          把一个 <code className="font-mono text-xs">.trailshare</code> 文件拖进来，
          三条断言会在你的设备上逐条核对：谁发的、内容有没有被动过、链路是否连续。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files?.[0];
            if (file) void verifyFile(file);
          }}
          className={
            dragging
              ? 'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-primary bg-accent/70 px-6 py-8 text-center transition-colors'
              : 'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-border bg-muted/30 px-6 py-8 text-center transition-colors hover:border-primary/60 hover:bg-accent/60'
          }
        >
          <Upload className={dragging ? 'mb-1 h-6 w-6 text-primary' : 'mb-1 h-6 w-6 text-muted-foreground'} />
          <span className="text-sm font-medium">
            {dragging ? '松开即可验证' : '把信物文件拖到这里'}
          </span>
          <span className="text-xs text-muted-foreground">
            文件只在本机解析，不会上传任何内容
          </span>
          <input
            ref={inputRef}
            type="file"
            accept=".trailshare,application/json"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void verifyFile(file);
              // 清空 value：连续验证同一个文件时也触发 change
              event.target.value = '';
            }}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            disabled={verifying}
            onClick={() => inputRef.current?.click()}
          >
            {verifying ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            {verifying ? '正在本机验证…' : '选择文件并验证'}
          </Button>
          {verifying && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在本机核对签名与承诺…
            </p>
          )}
        </div>

        {readError && (
          <Alert variant="destructive">
            <AlertTitle>读不出这个信物</AlertTitle>
            <AlertDescription>{readError}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
          {verification.items.map((item) => (
            <CheckRow key={item.key} item={item} />
          ))}
        </div>

        {verification.fromName && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span>
              来自 <strong className="font-semibold">{verification.fromName}</strong>
            </span>
            {verification.shareId && (
              <span className="font-mono text-xs text-muted-foreground">{verification.shareId}</span>
            )}
          </div>
        )}

        <Alert variant={hasFail ? 'destructive' : allPass ? 'success' : hasUnknown ? 'info' : 'default'}>
          <AlertTitle>
            {hasFail ? '不要接受这个信物' : allPass ? '三项断言全部通过' : '验证完成，部分未判定'}
          </AlertTitle>
          <AlertDescription>
            {verification.summary}
            {allPass && (
              <>
                {' '}
                <strong className="font-semibold">
                  无需信任服务器：以上验证全部在你本机完成，没有访问任何服务端。
                </strong>
              </>
            )}
          </AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  );
}

function CheckRow({ item }: { item: CheckItem }) {
  const Icon = STATE_ICON[item.state];
  return (
    <div className="flex items-start justify-between gap-3 rounded-md px-2 py-2">
      <div className="flex items-start gap-2">
        <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${STATE_ICON_COLOR[item.state]}`} />
        <div className="space-y-0.5">
          <p className="text-sm font-medium leading-snug">{item.title}</p>
          <p className="text-xs leading-relaxed text-muted-foreground">{item.detail}</p>
          <p className="font-mono text-[11px] leading-relaxed text-muted-foreground/80">{item.technical}</p>
        </div>
      </div>
      <Badge variant={STATE_BADGE_VARIANT[item.state]} className="shrink-0">
        {CHECK_STATE_LABELS[item.state]}
      </Badge>
    </div>
  );
}
