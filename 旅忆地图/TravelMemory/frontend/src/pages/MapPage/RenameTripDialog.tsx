/** 「重命名行程」对话框 —— 真的改后端（`PATCH /api/v1/trips/{id}`）。
 *
 * ## 为什么值得单独一个组件
 *
 * 改名这件事小，但它踩在两个坑上：
 *   1. **输入框要预填当前标题**。空着让用户重打一遍，等于逼他把刚想好的名字
 *      再想一次；而预填的是后端那份值，不是本地猜的。
 *   2. **失败不能关对话框**。改名失败（网络断、行程刚被删）时把输入内容一起
 *      清掉，用户刚才打的那半句话就没了。所以失败只弹 toast，对话框留着。
 *
 * ## 一个必须知道的副作用
 *
 * 后端会把 `source` 从 `cluster` 提升成 `manual`（见 `renameTrip` 的注释），
 * 所以这趟行程从此**不再随新导入的照片自动更新**。这不是缺陷而是取舍：
 * 用户敲过的字不能被派生数据冲掉。UI 在对话框里明说这件事，
 * 免得用户在"为什么我的旅程不更新了"上困惑。
 */

import { useState } from 'react';
import { Loader2 } from 'lucide-react';

import { Button } from '../../components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { toast } from '../../components/ui/use-toast';
import { renameTrip } from '../../services/api/trips';

export interface RenameTripDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tripId: string;
  currentTitle: string;
  /** 改名成功后通知上层刷新列表。 */
  onRenamed: () => void | Promise<void>;
}

const MAX_TITLE = 128;

export function RenameTripDialog({
  open,
  onOpenChange,
  tripId,
  currentTitle,
  onRenamed,
}: RenameTripDialogProps) {
  const [title, setTitle] = useState(currentTitle);
  const [saving, setSaving] = useState(false);

  const trimmed = title.trim();
  const confirmEnabled = trimmed.length > 0 && trimmed !== currentTitle && !saving;

  async function submit(): Promise<void> {
    if (!confirmEnabled) return;
    setSaving(true);
    try {
      const updated = await renameTrip(tripId, trimmed);
      toast({
        variant: 'success',
        title: '已重命名',
        description: `「${updated.title}」${updated.source === 'manual' ? ' · 不再随导入自动更新' : ''}`,
      });
      onOpenChange(false);
      await onRenamed();
    } catch (err) {
      // 失败不关对话框：标题还在输入框里，用户改一下就能重试
      toast({
        variant: 'destructive',
        title: '重命名失败',
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>重命名行程</DialogTitle>
          <DialogDescription>起一个你自己认得出的名字。</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="trip-rename">旅程标题</Label>
          <Input
            id="trip-rename"
            value={title}
            maxLength={MAX_TITLE}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit();
            }}
          />
          <p className="text-xs text-muted-foreground">
            {trimmed.length}/{MAX_TITLE}
          </p>
        </div>

        <p className="rounded-lg border border-sky-300 bg-sky-50 px-3 py-2 text-xs leading-relaxed text-sky-900">
          改名之后，这趟行程会变成<strong>手工行程</strong>，不再随后续导入的照片自动更新 ——
          这是为了不让你敲的名字被自动聚类冲掉。
        </p>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">取消</Button>
          </DialogClose>
          <Button
            disabled={!confirmEnabled}
            onClick={() => void submit()}
            title={trimmed === currentTitle ? '标题没有变化' : ''}
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {saving ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
