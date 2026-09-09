# frontend（AI 指挥中心 · Vue 3 + Vite）

雄安车路云协同管控frontend。设计方向：现代 AI 指挥中心（Linear/Vercel 深色工具美学），
全中文，深/浅双主题，普通/专业两级视图。

## 运行

```bash
cd frontend
npm install          # 首次（国内镜像已配置）
npm run dev          # http://127.0.0.1:5173 （/api 与 /ws 自动代理到后端 :8000）
```

后端需先启动：`cd backend && python -m uvicorn app.main:app --port 8000`

## 构建

```bash
npm run build        # 输出 dist/（base='./'，可由 FastAPI 静态托管或任意静态服务器）
```

## 目录

```
src/
├── App.vue              布局：顶栏 + 左栏(指标/方案/事件) + 画布 + 右栏(Agent) + 底部对比
├── styles/              tokens.css（设计系统：深/浅双主题）+ base.css
├── stores/              Pinia：ui(主题/视图) sim(仿真) metrics(指标) agent(对话) events(事件)
├── api/                 http.js（REST 封装） ws.js（WebSocket 增量客户端）
└── components/
    ├── TopBar.vue       仿真控制条 + 路网选择 + 主题/视图切换
    ├── NetCanvas.vue    路网画布（Canvas 渲染层，v0 为示例拓扑）
    ├── AgentPanel.vue   Traffic Copilot 对话 + 工具回放 + 快捷指令
    ├── MetricsPanel.vue 实时指标卡
    ├── SchemePanel.vue  方案二参数（专业模式）
    ├── EventPanel.vue   扰动注入（专业模式）
    ├── CompareSection.vue 方案对比图（专业模式）
    └── ui/              AppButton / PanelCard
```

## 里程碑

- M1 v0 ✅：骨架 + 设计系统 + 主题/视图切换 + 示例占位
- M2 ✅：真实 GeoJSON 渲染（任意路网自适应）+ 多车道 + 车辆（WS 增量）+ 信号相位 + 缩放平移；
  后端新增 `GET /api/v1/networks`
- M3 ✅：共享 WS 实时层 + 指标面板实时化（趋势箭头 + 历史曲线）+ 控制条状态（步数/时间/按钮态）+
  路网上传 `POST /networks/upload`（任意 SUMO 路网）
- M4 ✅：Agent 真实对话 + 工具回放两级显示（普通=摘要 / 专业=完整 JSON）
- M5：对比图接真实评估数据 + 状态覆盖 + 设计评审 + 入 v4 包
