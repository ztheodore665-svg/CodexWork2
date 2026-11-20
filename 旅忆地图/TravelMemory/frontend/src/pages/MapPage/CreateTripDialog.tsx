/** 「新建旅程」对话框 —— 真的建出一趟**空**行程（`POST /api/v1/trips`）。
 *
 * ## 这一版为什么不再是禁用的
 *
 * 上一版的确认按钮是**禁用**的，旁边写着「空旅程需要先导入照片」：那时后端
 * 只有 `GET /api/v1/trips`，没有任何写入接口，按钮点了也没地方去。
 * **与其伪造一个「已创建」，不如禁用并说明原因** —— 那是对当时事实的诚实表达。
 *
 * 现在后端补上了 `POST /api/v1/trips`（`source = "manual"`），事实变了，
 * 所以按钮解开了。**禁用不是一种设计风格，它只是"还做不到"的临时标记**，
 * 接口一到位就该撤掉，而不是继续留着当免责声明。
 *
 * ## 为什么手工行程不会被后续导入冲掉
 *
 * 后端 `rebuild_user_trips()` 重建行程时**只删 `source === 'cluster'` 的行**
 * （见 `backend/app/services/trip_service.py::_delete_user_trips`）。
 * 用户敲下的标题是库里唯一的副本，删了就找不回来 ——
 * 派生数据可以让位，用户输入不行。所以"先建旅程再导照片"是安全的操作顺序。
 *
 * ## 失败一律让用户看见
 *
 * 创建失败（网络断、后端 404 因为开关没开）**必须弹出来**，不能静默关掉对话框：
 * 静默的结果是"我明明点了创建，列表里却没有"，而用户会把它记成数据丢了。
 */

import { useState } from 'react';
import { ImageDown, Info, Loader2 } from 'lucide-react';

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
import { createTrip } from '../../services/api/trips';

export interface CreateTripDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 「去导入照片」：关掉本对话框并拉起文件选择器。由 MapActionPanel 注入。 */
  onImportPhotos?: () => void;
  /** 创建成功后通知上层刷新行程列表（新行程要立刻出现在列表里）。 */
  onCreated?: () => void;
}

const MAX_TITLE = 40;

export function CreateTripDialog({
  open,
  onOpenChange,
  onImportPhotos,
  onCreated,
}: CreateTripDialogProps) {
  const [title, setTitle] = useState('');
  const [creating, setCreating] = useState(false);

  const trimmed = title.trim();
  // 空白标题后端会 422，这里先拦一道：让按钮的可用性就是"能不能提交"的真相
  const confirmEnabled = trimmed.length > 0 && !creating;

  async function submit(): Promise<void> {
    if (!confirmEnabled) return;
    setCreating(true);
    try {
      const trip = await createTrip(trimmed);
      toast({
        variant: 'success',
        title: '旅程已创建',
        description: `「${trip.title}」—— 导入照片后会自动归到这趟行程。`,
      });
      setTitle('');
      onOpenChange(false);
      onCreated?.();
    } catch (err) {
      // 失败不关对话框：标题还在输入框里，用户改一下就能重试
      toast({
        variant: 'destructive',
        title: '创建失败',
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>新建旅程</DialogTitle>
          <DialogDescription>
            给这趟旅行起个名字。创建后可以先导入照片，也可以之后再补。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="trip-title">旅程标题</Label>
          <Input
            id="trip-title"
            value={title}
            maxLength={MAX_TITLE}
            placeholder="例如：洛阳两日"
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit();
            }}
          />
          <p className="text-xs text-muted-foreground">
            {trimmed.length}/{MAX_TITLE}
          </p>
        </div>

        <div className="flex gap-2 rounded-lg border border-sky-300 bg-sky-50 px-3 py-2 text-xs leading-relaxed text-sky-900">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>
            创建的是一趟<strong>空行程</strong>：照片要另外导入。手工创建的行程
            <strong>不会被后续导入冲掉</strong>，所以先建旅程再导照片是安全的。
          </p>
        </div>

        <DialogFooter>
          {onImportPhotos && (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onOpenChange(false);
                onImportPhotos();
              }}
            >
              <ImageDown className="h-4 w-4" />
              去导入照片
            </Button>
          )}
          <DialogClose asChild>
            <Button variant="ghost">取消</Button>
          </DialogClose>
          <Button
            disabled={!confirmEnabled}
            onClick={() => void submit()}
            title={trimmed.length > 0 ? '' : '请先填写旅程标题'}
          >
            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
            {creating ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
