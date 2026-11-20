/** 身份卡片：展示本机 Ed25519 公钥 + 重置身份（带破坏性确认对话框）。
 *
 * ## 为什么公钥要完整显示
 *
 * 公钥是唯一用来"加好友 / 指定收件人"的东西，截断到 16 位就只能让用户手抄一半。
 * 它确实长（72 字符），所以用**等宽字体 + 允许任意位置换行的容器**来消化，
 * 而不是砍短它 —— 砍短的用户只能回这里复制，多一步操作换来的观感不值。
 *
 * ## 为什么重置身份一定要确认对话框
 *
 * 换身份 = 换 X25519 私钥 = 之前别人发给你的信物**永久解不开**，没有任何恢复手段
 * （种子不导出、服务端没有副本）。这类不可逆操作必须用一个阻断性 UI 讲清后果。
 * 对话框的 Esc 关闭、焦点陷阱、关闭后焦点归还，全部交给 Radix ——
 * 手写这三件事必错，而错误方向通常是"回车直接确认了没看清的警告"。
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Check, Copy, KeyRound, RotateCcw, ShieldCheck } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '../../components/ui/alert';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '../../components/ui/card';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { toast } from '../../components/ui/use-toast';
import type { Identity } from '../../types/share';

/** 重置确认对话框里的原话。逐字固定：这是"后果说明"，不是可以随手润色的提示文案。 */
const RESET_WARNING = '重置后，之前发给您的信物将无法解密，且不可恢复。是否继续？';

/** 复制成功后图标回弹的时间。够看到 ✓，又不会让用户以为按钮坏了。 */
const COPIED_FEEDBACK_MS = 2000;

/** 复制到剪贴板。
 *
 * `navigator.clipboard` 只在**安全上下文**（https / localhost）里存在，
 * 内网 http 预览、老 WebView 里是 undefined；非安全上下文还得走 `execCommand` 兜底。
 * 两条路都失败就明确告诉用户手动复制 —— 静默失败的复制按钮最骗人：
 * 用户以为粘贴板里有了，结果贴了一串旧内容。 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 权限被拒 / 页面未聚焦：落到下面的兜底
  }

  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export interface IdentityCardProps {
  identity: Identity;
  /** 用户已在对话框里确认不可逆之后才调用（父负责清种子 + 重新派生）。 */
  onReset: () => void;
}

export function IdentityCard({ identity, onReset }: IdentityCardProps) {
  const [copied, setCopied] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);

  // ✓ 图标用完自动收回去：状态用 useEffect 管理，卸载时一并清掉定时器
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const fingerprint = identity.pubHex.slice(0, 8);

  const handleCopy = async () => {
    const ok = await copyText(identity.pubkey);
    if (!ok) {
      toast({
        variant: 'destructive',
        title: '没能写入剪贴板',
        description: '浏览器拒绝了剪贴板权限，请手动选中下方公钥复制。',
      });
      return;
    }
    setCopied(true);
    toast({
      variant: 'success',
      title: '公钥已复制',
      description: '发给好友，他就能把你加进收件人列表。',
    });
  };

  const handleConfirmReset = () => {
    setResetOpen(false);
    onReset();
  };

  return (
    <Card className="border-emerald-200 bg-gradient-to-br from-emerald-50/70 via-background to-background ring-1 ring-emerald-200">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-5 w-5 text-emerald-600" />
          我的身份
          <Badge variant="secure">本机生成</Badge>
        </CardTitle>
        <CardDescription>
          指纹 <span className="font-mono text-foreground">{fingerprint}</span> · 私钥只在本机，
          从不上传服务端。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="rounded-lg border border-border/60 bg-muted/50 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5" />
            公钥（给好友加你时用）
          </div>
          <p className="break-all font-mono text-xs leading-relaxed text-foreground">
            {identity.pubkey}
          </p>
        </div>

        <Button variant="outline" size="sm" onClick={() => void handleCopy()}>
          {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
          {copied ? '已复制' : '复制公钥'}
        </Button>
      </CardContent>

      <CardFooter className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <span className="text-xs text-muted-foreground">
          重置会生成全新密钥对，旧身份发出的信物不受影响，但发给旧身份的信物将永久无法解开。
        </span>
        <Button variant="destructive" size="sm" onClick={() => setResetOpen(true)}>
          <RotateCcw className="h-4 w-4" />
          重置身份
        </Button>
      </CardFooter>

      {/* 受控 Dialog：开关状态在 React 里，Esc / 遮罩 / 焦点管理交给 Radix */}
      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>不可逆操作</DialogTitle>
            <DialogDescription>请确认后再继续。</DialogDescription>
          </DialogHeader>

          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>重置身份</AlertTitle>
            <AlertDescription>{RESET_WARNING}</AlertDescription>
          </Alert>

          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button variant="destructive" onClick={handleConfirmReset}>
              我已了解，继续重置
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
