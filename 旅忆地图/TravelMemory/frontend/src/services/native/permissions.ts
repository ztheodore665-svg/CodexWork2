/** 权限查询与申请：Web 实现 + Native 桩。
 *
 * ## Web 端为什么大部分能力只能返回 'prompt'
 *
 * 浏览器**没有**统一的相册/相机权限 API。`navigator.permissions` 规范里只定义了
 * `geolocation`、`notifications`、`camera`、`microphone` 等少数几种，而且实际支持面很乱
 * （Firefox 至今不支持 'camera'）。相册更是完全没有------Web 读相册靠 `<input type="file">`
 * 唤起系统文件选择器，**用户选中即授权，压根没有"先问权限"这一步**。
 *
 * 所以 photos/camera 一律返回 `'prompt'`：既不是 granted（我们确实不知道），
 * 也不是 denied（不能让 UI 以为被拒了从而挡住入口）。语义是
 * **"我会在真正用到的那一刻才问用户"**，这正是 Web 的真实行为。
 *
 * location 是唯一能真查的：`navigator.permissions.query({name:'geolocation'})`。
 * 但它也会在部分浏览器上抛 TypeError（不支持的 name），必须 try/catch 兜到 'unsupported'，
 * 否则一个不支持的浏览器会让整个相册页崩在初始化阶段。
 */

import type { PermissionName, PermissionState, Permissions } from '../../types/native';

/** 规范化 geolocation 的 Permissions API 结果。
 *
 * 抽出来是因为 `request` 走的是 getCurrentPosition，只有成功/失败两种结果，
 * 而 `query` 要靠 Permissions API 拿到三态。两条路径最终都收敛到这张表上。
 */
function toPermissionState(status: PermissionStatus | undefined): PermissionState {
  switch (status?.state) {
    case 'granted':
      return 'granted';
    case 'denied':
      return 'denied';
    case 'prompt':
      return 'prompt';
    default:
      return 'unsupported';
  }
}

export function createWebPermissions(): Permissions {
  async function queryLocation(): Promise<PermissionState> {
    // Node/测试环境里 `navigator` 本身就不存在，直接引用会 ReferenceError
    if (typeof navigator === 'undefined') return 'unsupported';
    const permissionsApi = navigator.permissions;
    if (!permissionsApi?.query) return 'unsupported';
    try {
      const status = await permissionsApi.query({ name: 'geolocation' });
      return toPermissionState(status);
    } catch {
      // 浏览器不认识 'geolocation' 这个 name（或要求用户手势）时会抛
      return 'unsupported';
    }
  }

  async function requestLocation(): Promise<PermissionState> {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return 'unsupported';
    return new Promise<PermissionState>((resolve) => {
      navigator.geolocation.getCurrentPosition(
        () => resolve('granted'),
        (_error) => {
          // PERMISSION_DENIED=1 是明确拒了；超时/定位不可用本质上是"这次没拿到"，
          // 但对调用方来说结果一样：不能继续往下走。统一落到 'denied'，
          // 让 UI 显示"需要位置权限"，而不是卡在一个永远等不到 granted 的状态。
          resolve('denied');
        },
        { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
      );
    });
  }

  return {
    async query(name: PermissionName): Promise<PermissionState> {
      if (name === 'location') return queryLocation();
      // photos / camera：见文件头，Web 侧没有可查询的权限位
      return 'prompt';
    },

    async request(name: PermissionName): Promise<PermissionState> {
      if (name === 'location') return requestLocation();
      // 相册/相机的"申请"就是唤起选择器本身，结果只有用户选完才知道，
      // 所以这里不改变状态，保持 'prompt'
      return 'prompt';
    },
  };
}

/** Native 权限（未接入的占位实现）。
 *
 * 明早接真机时要替换的内容：
 *   1. 装 `@capacitor/core`，photos 用一个薄插件（社区方案：`@capacitor-community/media`，
 *      或自写 iOS PhotoKit / Android PermissionX），Josh 官方并没有专门的 photos 插件
 *   2. iOS：`AuthorizationStatus.limited` 很关键------用户只授权部分照片时，
 *      系统仍会允许选图。要把它映射成 `'granted'`（否则用户会看到"没权限"但明明能选），
 *      同时提示可以走 `presentLimitedLibraryPicker` 调整范围
 *   3. Android 13+ 用 Photo Picker 时**不需要** READ_MEDIA_IMAGES，直接回 'granted'；
 *      低于 13 才是真权限模型
 *   4. `request` 必须是同步于用户手势的调用链（iOS 系统会强制校验），
 *      不要包在 setTimeout / await 长任务之后
 */
export function createNativePermissions(): Permissions {
  const NOT_READY = '原生权限未接入：请先集成 Capacitor 并实现该桥接';
  return {
    async query(_name: PermissionName): Promise<PermissionState> {
      throw new Error(NOT_READY);
    },
    async request(_name: PermissionName): Promise<PermissionState> {
      throw new Error(NOT_READY);
    },
  };
}
