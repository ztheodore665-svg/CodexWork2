# 高德 JS API Key 部署与防护

本文覆盖四件事：**域名白名单**、**安全密钥（securityJsCode）的注入时机**、
**配额告警**、**Key 泄漏后的轮换**。适用于仓库里所有用到高德 Web 端 JS API 的宿主：

| 宿主 | 读取 Key 的位置 | 消费方 |
| --- | --- | --- |
| 主应用 `frontend`（地图页 / 补标 / 回放） | `import.meta.env.VITE_AMAP_JS_KEY`（`frontend/.env.example`） | `frontend/src/pages/MapPage/amap.ts` → `@travelmemory/amap-loader` |
| Track Studio `apps/track-studio` | 同上（`apps/track-studio/src/env.ts` + `map/baseMap.ts`） | `map/baseMap.ts` → `@travelmemory/amap-loader` |
| TrailShare 独立站 `apps/trailshare-standalone` | 同上（内嵌模式复用主应用的底图） | 同 Track Studio |

> 安全前提：**Web 端 JS Key 是公开值**。它会被打进产物，任何人打开 DevTools 都能读到。
> 它的唯一防线是「域名白名单 + 安全密钥」。真正的机密是服务端用的 `AMAP_API_KEY`
> （REST），那把 Key **绝不能**出现在任何 `VITE_` 变量里。

---

## 1. 域名白名单（控制台）

路径：<https://console.amap.com/dev/key/app> → 选应用 → 「Web 端 (JS API)」→ 编辑 Key。

1. **开发环境**：把本地域名写进白名单，否则 dev server 下地图永远是灰的（控制台报
   `INVALID_USER_DOMAIN` / `USERKEY_PLAT_NOMATCH`）：
   ```
   localhost
   127.0.0.1
   ```
   高德按**域名**（不含端口、不含协议）匹配，所以 `http://localhost:5173` 与
   `http://localhost:5174`（Track Studio）共用一条 `localhost` 即可。
2. **生产环境**：写**真实域名**，一条一行，例如：
   ```
   travelmemory.example.com
   m.travelmemory.example.com
   ```
   带端口、带路径、带通配 `*` 都无效（高德不支持通配符子域名匹配之外的花样写法；
   需要多个子域就逐条列出）。
3. **不要**为了省事填 `*` 或不填：那等于把 Key 交给任何站点引用。
4. 改完白名单**立即生效**，不需要重新发版（白名单在服务端判断）。

> 内嵌场景：Track Studio 以 iframe 方式嵌在主应用里时，请求的 `Referer` 是
> **iframe 自己的 origin**（dev 是 `localhost:5174`），所以两个端口都要在
> 「localhost」这一条白名单覆盖范围内。

---

## 2. 安全密钥：必须在加载 JS API **之前**注入

这是最容易做错的一步，错了不会报错，只会出现「安全密钥不生效」的静默失败。

### 规则

高德 JS API 2.0 只认下面这个**字段名**：

```js
window._AMapSecurityConfig = { securityJsCode: '<控制台的安全密钥>' };
```

两个要点：

1. **字段名是 `securityJsCode`**，不是 `securityCode`。写成后者等于没配
   （仓库合并前就踩过这个坑，见 `frontend/src/pages/MapPage/amap.ts` 文件头）。
2. 这段赋值必须发生在 `<script src="https://webapi.amap.com/maps?...">`
   **插入并开始执行之前**。JS API 在初始化那一刻读取该全局值，事后再设已经晚了。

### 本仓库的落点（已确认，行号可核对）

```
packages/amap-loader/src/loader.ts
  L121   const securityJsCode = options.securityJsCode?.trim();
  L123   if (securityJsCode || serviceHost) {
  L124     g._AMapSecurityConfig = {
  L125       ...(securityJsCode ? { securityJsCode } : {}),
  L126       ...(serviceHost ? { serviceHost } : {}),
  L127     };
  L128   }
  ...
  L155   document.head.appendChild(script);   ← 插脚本发生在赋值之后
```

即：`_AMapSecurityConfig` 在 **L124** 写入，`script` 在 **L155** 才被 append。
顺序正确，无需额外处理。

两条调用链都把密钥透传到这个选项：

```
apps/track-studio/src/map/baseMap.ts
  L113   return loadAmapScript<AMapNamespace>({
  L115     securityJsCode: readEnvSecurityCode(),

frontend/src/pages/MapPage/amap.ts
  L97    securityJsCode: securityCode,
```

### 新人最容易犯的三个错

| 错误 | 后果 |
| --- | --- |
| 写成 `securityCode` | 密钥不生效，控制台报 `INVALID_USER_SCODE` |
| 在 `useEffect` 里设 `window._AMapSecurityConfig`（地图已加载） | 本次加载已取不到，本次会话全废 |
| 在 `index.html` 里用内联 `<script>` 设值但 CSP 挡了内联脚本 | 赋值被 CSP 拦掉，密钥同样不生效（本仓库的 CSP 是 `script-src 'self'`，生产不允许内联） |

> 生产替代方案：不想把安全密钥放前端，可以在服务端把高德的 Web 服务域名代理出去，
> 然后把代理地址传给 `loadAmapScript` 的 `serviceHost` 选项
> （同样在 L124 那段一起写进 `_AMapSecurityConfig`）。

---

## 3. 配额与告警

个人开发者（2025-05 起的新规）的免费额度量级：

| 能力 | 额度（个人开发者） |
| --- | --- |
| 基础地图 / 定位（JS API 地图加载） | **150 万次 / 月** |
| LBS 服务（地理编码、路径规划、搜索等 Web 服务） | **15 万次 / 月** |

超出后不是简单限流：通常是**当天直接返回错误**，表现为「地图突然全灰」或
「补标页一片空白」，而代码没有任何改动 —— 这类问题排查成本极高，所以必须提前告警。

做法（控制台 <https://console.amap.com/dev/budget> 与「配额管理」页）：

1. **设阈值邮件告警**：分别对「地图加载量」与「Web 服务调用量」设 50% / 80% / 95%
   三档，收件人填值班邮箱（不要只填个人邮箱）。
2. **打开「配额用尽通知」**：默认关闭的账号必须手动开。
3. 本项目自身的**放大因素**要一起估：
   - 地图页每刷新一次 = 1 次地图加载；`MapCanvas` 与回放层**互相卸载/重挂**，
     在回放反复开关时会各算一次；
   - 补标 / 补全位置页每开一次也要一次地图加载；
   - 内嵌 Track Studio 的 iframe 有**自己的地图实例**，等于叠加一次。
   演示 / QA 环境建议**单独申请一把 Key**，避免把生产额度打光。
4. 如果确实要跑压测：给测试环境配一把白名单只含 `localhost` 的 Key，测完禁用。

---

## 4. Key 泄漏后的轮换步骤

判定泄漏的常见信号：控制台配额曲线在无人使用时段暴涨、出现陌生域名报
`INVALID_USER_DOMAIN`、GitHub / 公开产物里搜到了 `key=` 后面的字符串。

轮换顺序（**先建新、再切流、最后禁旧**，避免中间出现「地图全灰」的窗口）：

1. **新建一把 Key**：控制台 → 添加 Key → 平台选「Web 端 (JS API)」，
   白名单按第 1 节填好（只填真实域名）。同一个 Key 也可以用「安全密钥」配套
   单独申请一个新的 `securityJsCode`。
2. **先在测试 / 预发布环境换上**：改环境变量（不进代码仓库）：
   ```
   VITE_AMAP_JS_KEY=<新 Key>
   VITE_AMAP_SECURITY_CODE=<新安全密钥>
   ```
   重新构建（`VITE_` 变量是**构建期**注入，改完必须重新 build，热更新不会生效）。
3. **验证**：打开地图页与补标页，确认底图正常、`window._AMapSecurityConfig`
   里是新值、控制台无 `INVALID_USER_SCODE` / `INVALID_USER_DOMAIN`。
4. **生产切流**：把新 Key 写进部署环境的 secret / CI 变量，重新发布。
   若使用灰度，先小流量观察 30 分钟配额曲线是否正常。
5. **禁用旧 Key**：确认线上没有请求还在用旧 Key（看控制台的域名与量级曲线，
   归零之后再禁用）。**不要立刻删除** —— 删了无法回滚；先「禁用」，观察一周再删。
6. **收尾**：
   - 检查泄漏源（提交历史 / 打包产物 / 截图 / 分享出去的 `dist`），
     把它挪到环境变量或 secret；
   - 如果旧 Key 是服务端 Key（`AMAP_API_KEY`），**轮换优先级最高**：
     它没有域名白名单保护，泄漏即为他人免费调用你的额度；
   - 记录这次轮换的时间与责任人到运维记录里（下次审计要有据可查）。

> 提醒：`VITE_` 前缀的变量**永远**会被打进前端产物，任何「把 Key 藏在环境变量里
> 别人就看不到」的想法都是错的。前端 Key 的安全性完全来自白名单 + 安全密钥 +
> 配额告警这三件套。
