/** 「信物分享」对话框 —— 把整页的 SharePage 装进一个 Dialog。
 *
 * ## 为什么是"整个装进去"而不是拆成几个弹窗
 *
 * 产品化之后信物分享不再是独立页面，但它的内容是一整条**有顺序的流程**：
 * 看公钥 → 加好友 → 选收件人 → 生成 → 下载，中间还要能导入/验证别人发来的信物。
 * 拆成四五个各自独立的 Dialog，用户点一下弹一个、看完关一个，
 * 走到第三步就已经忘了自己选过谁。装进一个带滚动的 Dialog，
 * 流程上下文始终可见，也复用了 SharePage 里全部既有逻辑与测试。
 *
 * ## 为什么用 Dialog 而不是 Sheet
 *
 * 信物分享是**模态任务**（做完就走），抽屉是**常驻面板**（可以一直开着对照地图）。
 * 语义不同：开着一个分享抽屉会让"地图上刚导入的行程"被挡住一半，
 * 而用户此时正需要看着地图确认收件人范围。
 */

import { useEffect } from 'react';
import { SharePage } from '../../pages/SharePage';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { ScrollArea } from '../../components/ui/scroll-area';
import type { TripView } from '../../services/api/trips';
import type { UnpackedPlace } from '../../types/share';

/** 打开对话框后直接跳到哪一段。地图工具栏上「生成信物」「验证信物」
 *  两个按钮共用同一个对话框，差别只在落地位置 —— 用户点了"验证"
 *  就该看到验证那三条断言，而不是又从公钥看起。 */
export type ShareSection = 'generate' | 'verify';

export interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trips: readonly TripView[];
  /** 信物解出的地点 → 交给地图画模糊圈。 */
  onViewOnMap: (places: UnpackedPlace[]) => void;
  section?: ShareSection;
}

export function ShareDialog({ open, onOpenChange, trips, onViewOnMap, section }: ShareDialogProps) {
  // 每次打开都重新定位：用户第二次点「验证信物」时，上一次可能停在导入区
  useEffect(() => {
    if (!open || !section) return;
    // 等一帧：Dialog 的内容是在 open 变 true 之后才挂上 DOM 的，
    // 同一帧里 querySelector 只会找到 null
    const raf = requestAnimationFrame(() => {
      document.getElementById(`share-${section}`)?.scrollIntoView({ block: 'start' });
    });
    return () => cancelAnimationFrame(raf);
  }, [open, section]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 p-0">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle>信物分享</DialogTitle>
          <DialogDescription>
            定向加密信物（TrailShare 协议 ts-2026.4）：只有指定收件人能解开，私钥只在本机。
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="min-h-0 flex-1 px-6 py-4">
          <SharePage trips={trips} onViewOnMap={onViewOnMap} />
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
