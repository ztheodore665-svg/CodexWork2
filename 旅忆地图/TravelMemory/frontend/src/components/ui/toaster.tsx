import {
  Toast,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from './toast';
import { useToast } from './use-toast';

/** 全局 toast 渲染器。**整个 App 挂一次**（`App.tsx` 里）即可。
 *
 * 刻意放在 `components/ui` 而不是各页面内部：同一时刻只需要一个 viewport，
 * 挂多份会导致后来的 toast 被早期 viewport 盖住（z-index 相同、DOM 顺序决定层叠）。
 */
export function Toaster() {
  const { toasts } = useToast();

  return (
    <ToastProvider swipeDirection="right" duration={5000}>
      {toasts.map((item) => (
        <Toast key={item.id} variant={item.variant}>
          <div className="grid flex-1 gap-1">
            {item.title ? <ToastTitle>{item.title}</ToastTitle> : null}
            {item.description ? <ToastDescription>{item.description}</ToastDescription> : null}
          </div>
          {item.action}
          <ToastClose />
        </Toast>
      ))}
      <ToastViewport />
    </ToastProvider>
  );
}
