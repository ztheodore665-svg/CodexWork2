import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Tailwind 类名合并。
 *
 * 为什么不能只写 `clsx`：组件对外暴露 `className` 时，调用方传的
 * `px-4` 与组件内置的 `px-2` 会**同时**出现在 class 里，最终谁生效取决于
 * CSS 里的声明顺序而不是书写顺序 —— 这是一类极难排查的样式覆盖 bug。
 * `twMerge` 按 Tailwind 的冲突分组保留后者，语义才等于"调用方覆盖默认"。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
