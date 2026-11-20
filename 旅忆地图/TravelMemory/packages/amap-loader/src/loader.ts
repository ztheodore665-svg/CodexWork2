/** 高德 JS API 2.0 的动态加载原语。
 *
 * ## 这一层只管「把 SDK 弄到手」，不管拿到之后怎么办
 *
 * 两个宿主对失败的态度不一样：主应用要把错误抛给 UI（显示「未配置 Key」占位卡），
 * 展示站要静默降级成自绘格网。所以**策略留在宿主，机制放这里**：去重、超时、
 * script 复用、安全配置、Node 环境守卫，只有一份实现。
 *
 * ## 为什么不用 `declare global` 声明 `window.AMap`
 *
 * 两个宿主各自声明了不同形状的 `AMap` 命名空间（主应用要 Marker/Circle，
 * 展示站要 LngLat）。如果这里再 declare 一次，TypeScript 会要求同名属性类型完全一致
 * 而直接报错。所以内部用一次局部断言读取，不污染全局声明 —— 宿主爱声明什么形状都行。
 *
 * ## 安全密钥字段是 `securityJsCode`，不是 `securityCode`
 *
 * 高德官方文档（JS API 2.0「安全密钥使用」）明文写的是
 * `window._AMapSecurityConfig = { securityJsCode: '…' }`。
 * 合并前 `frontend/amap.ts` 误写成 `securityCode`，配了密钥也不会生效 —— 这个包顺带修掉它。
 * 另支持 `serviceHost`（生产环境推荐的代理转发方式）。
 */

const SCRIPT_URL = 'https://webapi.amap.com/maps?v=2.0&key=';
const DEFAULT_TIMEOUT_MS = 15000;
/** 给插入的 script 打标记，避免重复插标签（StrictMode 双挂载 / 上次超时后重试） */
const SCRIPT_FLAG = 'data-amap-loader';

/** 命名空间的最小形状。宿主可以用泛型传入自己那份更具体的接口。 */
export interface AmapNamespaceLike {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => unknown;
}

export interface LoadAmapOptions {
  /** 高德 JS API key（浏览器端公开值，靠 referer 白名单保护，不是机密） */
  key: string;
  /** 安全密钥；官方字段名 securityJsCode，必须在插脚本之前设置 */
  securityJsCode?: string;
  /** 代理转发地址（生产推荐），与 securityJsCode 二选一或同时给 */
  serviceHost?: string;
  /** 超时毫秒数。主应用 15s，展示站 8s */
  timeoutMs?: number;
}

interface GlobalWithAmap {
  AMap?: unknown;
  _AMapSecurityConfig?: { securityJsCode?: string; serviceHost?: string };
}

function globalWithAmap(): GlobalWithAmap | null {
  if (typeof window === 'undefined') return null;
  return window as unknown as GlobalWithAmap;
}

/** 成功加载后缓存的命名空间；失败不缓存。 */
let cached: unknown = null;
/** 并发去重：同一时刻所有调用共享这一个 Promise。 */
let loading: Promise<unknown> | null = null;

/** 供 UI 同步判断，避免地图容器闪烁。 */
export function isAmapReady(): boolean {
  return !!globalWithAmap()?.AMap || cached !== null;
}

/** 测试专用：清掉去重与成功缓存。`beforeEach` 用，避免用例间互相污染。 */
export function resetAmapLoaderForTest(): void {
  cached = null;
  loading = null;
}

/**
 * 加载高德 JS API 并返回 `window.AMap`。
 *
 * 统一契约：**失败一律 reject**。需要降级的宿主自己 `.catch(() => null)`，
 * 这样错误原因不会被吞掉，也便于写测试。
 *
 * @throws 无 window/document、key 为空、脚本超时、`window.AMap` 未定义、脚本 onerror
 */
export function loadAmapScript<T extends AmapNamespaceLike = AmapNamespaceLike>(
  options: LoadAmapOptions,
): Promise<T> {
  const g = globalWithAmap();
  if (!g || typeof document === 'undefined') {
    return Promise.reject(new Error('高德地图只能在浏览器环境加载（当前没有 window/document）'));
  }
  // 别的模块（或上次超时后才到达的脚本）已经把 SDK 挂上了，直接复用
  if (g.AMap) return Promise.resolve(g.AMap as T);
  if (cached !== null) return Promise.resolve(cached as T);

  const key = options.key.trim();
  if (!key) {
    return Promise.reject(new Error('未配置高德 JS API Key（VITE_AMAP_JS_KEY）—— 地图不可用，但其余功能不受影响'));
  }
  if (loading) return loading as Promise<T>;

  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  loading = new Promise<unknown>((resolve, reject) => {
    let settled = false;
    let timer = 0;

    const finish = (failure: Error | null): void => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      // 无论成败都释放去重锁，让下一次调用有机会重试
      loading = null;
      if (failure) {
        reject(failure);
        return;
      }
      const namespace = g.AMap;
      if (!namespace) {
        reject(new Error('高德脚本已加载但 window.AMap 未定义 —— key 可能无效'));
        return;
      }
      cached = namespace;
      resolve(namespace);
    };

    // 安全配置必须在插入 script 之前写，晚了该次加载就取不到
    const securityJsCode = options.securityJsCode?.trim();
    const serviceHost = options.serviceHost?.trim();
    if (securityJsCode || serviceHost) {
      g._AMapSecurityConfig = {
        ...(securityJsCode ? { securityJsCode } : {}),
        ...(serviceHost ? { serviceHost } : {}),
      };
    }

    // 上一次超时留下的 script 可能还在路上：复用它，不要再插一个
    const existing = document.querySelector<HTMLScriptElement>(`script[${SCRIPT_FLAG}]`);
    const script = existing ?? document.createElement('script');

    timer = window.setTimeout(() => {
      // 超时那一刻脚本可能刚好执行完，先给一次机会
      if (g.AMap) {
        finish(null);
        return;
      }
      // 移除死标签：否则下次会复用一个 onload 已经不会触发的 script，永久卡到超时
      script.remove();
      finish(new Error(`高德地图脚本加载超时（${timeoutMs}ms）—— 检查网络或 key 的 referer 白名单`));
    }, timeoutMs);

    script.onload = () => finish(null);
    script.onerror = () => {
      script.remove();
      finish(new Error('高德地图脚本加载失败 —— 检查网络连接'));
    };

    if (!existing) {
      script.src = `${SCRIPT_URL}${encodeURIComponent(key)}`;
      script.async = true;
      script.setAttribute(SCRIPT_FLAG, '1');
      document.head.appendChild(script);
    }
  });

  return loading as Promise<T>;
}
