import * as React from 'react';

import type { ToastActionElement, ToastVariant } from './toast';

/** Toast 状态机。
 *
 * 为什么用一个模块级 store 而不是 React Context：
 * 触发提示的位置常常在**回调深处**（打包完成的 `await` 之后），那里不该为了弹一条
 * 消息去接一层 Provider。`toast()` 是一个可以从任何地方调用的纯函数，
 * `<Toaster />` 在整个 App 挂一次即可。
 *
 * 用发布订阅而不是 `useState` 提升：多个组件同时订阅时，`dispatch` 只需
 * 遍历监听器广播一次，不会因为谁先 setState 而丢消息。
 */

const TOAST_LIMIT = 3;
/** 自动消失前的停留时长。够读完一整句中文提示，又不至于挡住表单。 */
const TOAST_REMOVE_DELAY = 5000;

export interface ToasterToast {
  id: string;
  title?: string;
  description?: string;
  variant?: ToastVariant;
  action?: ToastActionElement;
}

type Listener = (state: ToasterToast[]) => void;

let memoryState: ToasterToast[] = [];
const listeners = new Set<Listener>();
const timeouts = new Map<string, ReturnType<typeof setTimeout>>();

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `toast-${idCounter}`;
}

function emit(next: ToasterToast[]): void {
  memoryState = next;
  for (const listener of listeners) listener(memoryState);
}

export function toast(input: Omit<ToasterToast, 'id'>): string {
  const id = nextId();
  const record: ToasterToast = { ...input, id };

  emit([record, ...memoryState].slice(0, TOAST_LIMIT));

  timeouts.set(
    id,
    setTimeout(() => {
      timeouts.delete(id);
      dismiss(id);
    }, TOAST_REMOVE_DELAY),
  );

  return id;
}

export function dismiss(id?: string): void {
  const timer = id === undefined ? undefined : timeouts.get(id);
  if (timer !== undefined) {
    clearTimeout(timer);
    timeouts.delete(id as string);
  }
  emit(id === undefined ? [] : memoryState.filter((item) => item.id !== id));
}

/** 订阅当前 toast 列表。返回 unsubscribe（用于 `useEffect` 清理）。 */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useToast(): {
  toast: typeof toast;
  dismiss: typeof dismiss;
  toasts: ToasterToast[];
} {
  const [state, setState] = React.useState<ToasterToast[]>(memoryState);

  React.useEffect(() => subscribe(setState), []);

  return { toast, dismiss, toasts: state };
}
