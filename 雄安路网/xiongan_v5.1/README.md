# 车路云协同管控平台（雄安 · 城市大脑）

XH-202613 竞赛赛道 C（AI 应用型）：以 **SUMO 微观交通仿真**为底座、**MAPPO 强化学习 + 标准化信号控制算法**为核心、**LLM 智能体**为交互入口的车路云一体化协同管控平台。

> 本文件为**平台使用与运行的全部说明**；正式提交报告与接口/评估文档见文末《六、文档索引》。

---

## 一、目录结构

```
├── start.bat                 Windows 一键启动（后端 8000 + 前端 5173）
├── docker-compose.yml        Docker 编排（backend + frontend，见 §四）
├── docker/                   两个 Dockerfile + nginx 反代配置
├── intersection_cases/       四典型路口案例集（demo_1~4，平台可加载演示）
├── backend/                  FastAPI 后端
│   ├── app/
│   │   ├── api/              REST + WebSocket 接口层
│   │   ├── core/             TraCI 引擎适配 / 会话 / 数据采集 / 运行时
│   │   ├── schemes/          算法：webster / 方案一 / 二(MAPPO·SCOOT) / 三
│   │   ├── algorithms/       标准化算法接口（MCP-like）
│   │   ├── agent/            LLM 智能体（14 工具 + 记忆）
│   │   ├── report/           评估报告生成（md/docx/pdf + matplotlib）
│   │   ├── eval/             评分公式（效率^0.5·稳定^0.3·公平^0.2）
│   │   ├── events/ metrics/ ws/  事件注入 · 指标存储 · 推送
│   │   └── main.py           应用入口
│   ├── models/weights/       MAPPO(agnostic/legacy) 与 STGCN 权重 + ONNX
│   ├── scripts/              训练/实验/文档转换脚本
│   └── requirements.txt
├── frontend/                 Vue 3 + Vite 前端（Canvas 自绘 + ECharts）
├── networks/network/         SUMO 路网（base_network + 多套车流/配时）
├── docs/                     赛事提交文档（索引见 §六）
└── requirements/             赛题与需求
```

## 二、环境要求

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Windows | 10/11 | 已适配 |
| Python | 3.10+（开发用 3.14） | 后端 |
| Node.js | 18+ | 前端构建 |
| SUMO | 1.20+（测试 1.27.1） | Windows 需 `SUMO_HOME` 指向安装目录（如 `E:\`）；Linux 用 `sumo` 命令（engine 跨平台） |
| Ollama | 可选 | 本地离线 LLM（qwen2.5:1.5b/3b） |
| PyTorch | CPU 即可 | MAPPO/STGCN 推理；无权重自动降级 SCOOT |

后端依赖：`backend/requirements.txt`；前端随包已含 `node_modules`（缺失则 `npm install`）。

## 三、Windows 运行

### 3.1 一键启动
双击 `start.bat`：自动检查/安装 Python 依赖（清华镜像）→ 检查/安装前端依赖 → 起后端(8000)与前端(5173) → 打开浏览器。

### 3.2 手动启动
```bat
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev            :: 访问 http://localhost:5173
```

## 四、Docker 运行

```bash
docker compose up -d --build    # 构建并启动（首次较慢：torch 等依赖）
# 浏览器 http://localhost:5173；后端容器暴露 8000，nginx 反代 /api 与 /ws
docker compose logs -f backend   # 看日志；docker compose down 停止
```
- 后端容器内置 SUMO 与全部 Python 依赖，无需本机装 Python/Node/SUMO；
- 默认 LLM=本地 Ollama 模式；用智谱请在 `docker-compose.yml` 按注释配 `env_file: .env`；
- 若容器内 Debian 版 SUMO 与 TraCI 不兼容，将 `docker/backend.Dockerfile` 基础镜像换成 `eclipse/sumo:1.19.0`（官方镜像，含较新 sumo）。

## 五、使用说明

### 5.1 LLM 配置
`backend/.env`（不入库，缺失时默认 Ollama）：
```
LLM_PROVIDER=zhipu            # 智谱免费 API（推荐）或 ollama
LLM_MODEL=glm-4-flash         # glm-4-flash / glm-4.7-flash / ollama 模型
LLM_API_KEY=你的智谱密钥
```
也可在平台智能体栏"LLM 模型"下拉随时切换（免重启）。

### 5.2 典型演示流程
1. 打开页面即自动启动 `base_network`（20 路口）+ 官方方案 + 5× 速度（评委默认一键直达）；也可选择 `案例路口 demo_1~4`（单路口，顶部下拉直接可选）；
2. 右上角场景：四档峰期 + **区域热点（热点区饱和外溢、外围畅通，算法疏通的价值场景）**；`demo_1~4` 默认 Webster（自动按流量配时）；
3. 方案下拉：`webster`（仅 demo 显示）/ 方案一 / 方案二 MAPPO·SCOOT / 方案三 / **官方方案 mappo优化（20 路口专属，按官方早/平/晚三档配时逐相位重建并自动选档）** / 基线；多路口建议 `official` 或 `scheme_2`；默认专业模式 + 无框箭头极简灯 + 指标卡（基础四卡+最堵塞道路/路口+完成率）；刷新/重启后恢复上述默认，不保存上次界面调整；
4. 启动 → 预热约 90s 后画面渲染；实时指标卡（自定义指标可点"最堵塞道路/路口"定位画布）；
5. 对智能体下令："东侧拥堵，请调整最长绿灯并对比前后效果" → 观察 Agent 调 `configure_algorithm` 并给前后对比；
6. 底部"生成报告"导出 md/docx/pdf。

### 5.3 功能点速览
- **画布**：滚轮缩放/拖拽/双击复位；点击车辆（详情+驾驶建议 advice）、道路（流量/限速/加车）、路口（排队/相位）；
- **左侧**：实时指标（4 基础卡 + 自定义：完成率/最久车辆/最堵道路·路口，点击定位）、方案面板、事件注入（事故/施工/突发车流）、人工加车；设置内可切左栏"堆叠/单栏"；
- **右侧 Agent**：14 工具（感知/规划/算法配置/事件/前后对比）、模型切换、工具详情、清空记忆；
- **底部**：实时速度曲线、离线评估对比、综合评分、全屏数据、生成报告；
- **设置**：主题、普通/专业模式、极简信号灯、右转常绿等（条目式开关）。

## 六、文档索引（docs/，赛事提交材料）

| 文档 | 内容 | 对应赛题交付 |
| --- | --- | --- |
| 系统设计与算法报告.md | 总体方案、算法原理（三方案/LLM 见附录 B/C）、云边端（并入接口文档§15）、创新点、**路网接入规范（附录 E）** | 提交① |
| 实验评估报告.md | 对比实验、图表、**AI 训练与验证方法（附录）** | 提交③ |
| 接口文档.md | REST/WS 全端点、错误码、OpenAPI 导入、云-边-端架构与消息（§15） | 功能一·任务1 |
| 四典型路口最优调度方案.md | 单路口案例最优配时与验证 | 功能一·任务2 |
| pdf/ | 各报告的 PDF 导出 | 提交 |

## 七、常见问题

| 现象 | 处理 |
| --- | --- |
| 后端报 SUMO 错误 | Windows 检查 `SUMO_HOME`；Linux 确认 `sumo` 在 PATH |
| 启动后无车辆 | 确认选了带车流路网或场景；预热 90s 内画面空白正常 |
| 方案二显示 SCOOT | 权重未匹配自动降级；agnostic 需 `obs_mode=agnostic`（前端默认已配） |
| Agent 报 LLM 失败 | 查 `.env` 密钥；智谱临时限流可切 `glm-4-flash` |
| demo 看不到默认方案 | 默认方案(webster)仅对 demo 路口显示；其它路网请选方案一/二 |
| 端口占用 | 改 `start.bat` / `docker-compose.yml` 端口映射 |
| 评分"硬性筛选未通过" | 拥堵严重时的正常保护（LOS F/完成率<60%/等待>4 周期不评分） |
| 环境自检 | `python -c "import fastapi,uvicorn,openai,matplotlib,docx,reportlab"` 全过即依赖齐 |

## 八、技术栈速览

前端：Vue 3 + Vite + Pinia + ECharts + Canvas 2D（自绘路网/信号灯/车辆）
后端：FastAPI + WebSocket + TraCI(SUMO) + PyTorch(MAPPO) + STGCN + ONNX + OpenAI 兼容 LLM
算法：Webster 配时 / MAXBAND 绿波 / MAPPO(CTDE·GAE·PPO) / SCOOT / 动态 Dijkstra 车端引导 / LLM 分层协同
报告：matplotlib + python-docx + reportlab（md/docx/pdf）

---