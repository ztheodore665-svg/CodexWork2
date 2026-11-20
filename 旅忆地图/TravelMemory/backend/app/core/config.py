"""全局配置：所有环境变量的唯一入口。

约定（PROJECT.md §二、[5]）：**其他模块不得直接 os.getenv**，一律从这里取。
这样做的收益：换环境只改 .env；测试里可以方便整体替换 Settings；CI 能在启动时
立刻发现漏配，而不是运行到一半才 KeyError。

.env 查找顺序：项目根 .env → backend/.env（后者覆盖前者）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py → parents = [core, app, backend, project_root]
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """运行时配置。

    字段按阶段分组，当前阶段用不到的保持默认值即可，不影响跑通主流程。
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里未在本类声明的变量直接忽略，避免拼写错配导致的静默失效
        case_sensitive=False,
    )

    # ---------------- 基础（阶段 0） ----------------
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/travel_memory"
    #: 运行时。阶段 16 起默认 `graph`：`make eval` 与线上必须同源。
    #: 设成未知值或 langgraph 未安装时会回落 `pipeline`（降级而不是抛错）。
    agent_runtime: str = "graph"  # pipeline（阶段 0~3）| graph（阶段 4+）
    #: self_check 未通过后允许的**重试次数**（两种运行时共用这一个值）
    agent_generate_retries: int = 1
    log_level: str = "INFO"

    # ---------------- LLM（DeepSeek / Qwen，统一 OpenAI 兼容协议） ----------------
    # provider = auto：有 key 走真实 API，没 key 自动降级为 mock（打印醒目警告），
    # 这样阶段 0 在没有密钥时也能把整条管道跑通，方便先验证工程链路。
    llm_provider: str = "auto"  # auto | openai | mock
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    llm_timeout_sec: float = 60.0
    llm_max_retries: int = 2  # 仅对网络类错误重试，业务 4xx 不重试
    llm_daily_budget_cents: int = 2000  # 阶段 0 为进程内计数，阶段 3 换成 Redis 原子计数
    # ---- 阶段 2：SSE 流式（mock 用，让"首字可见"在本地也能验证）----
    mock_stream_chunk_chars: int = 12  # mock 每段的字符数
    mock_stream_delay_sec: float = 0.05  # 段间延迟，太小就看不出流式效果

    # ---------------- Embedding ----------------
    embedding_provider: str = "auto"  # auto | openai | hashing
    embedding_model: str = "bge-m3"
    embedding_model_version: str = "bge-m3@v1"  # 变更此值必须 make reindex
    embedding_dim: int = 1024  # 必须与实际模型维度一致，启动时会校验
    embedding_api_key: str = ""
    embedding_base_url: str = ""  # OpenAI 兼容端点，留空时跟随 LLM_BASE_URL
    embedding_batch_size: int = 16

    # ---------------- 检索 ----------------
    retrieval_top_k: int = 8  # 召回条数（构建上下文的上限）
    # 检索阈值：低于该相似度的证据不进上下文，防"硬凑引用"。
    #
    # **为什么默认值只有 0.01**（曾经是 0.30，是个会致命的默认值）：
    # 余弦相似度的绝对高度**完全取决于 provider** ——
    #   - 本地哈希向量：整体只有 0.05~0.13
    #   - 真实 embedding（qwen3.7-text-embedding-flash / bge-m3）：通常 0.5~0.9，
    #     但库里只要有几条"同主题但无关"的文档，顶部也可能被压到 0.2 以下
    # 用 0.30 这个"看起来安全"的阈值去套真实 embedding，表现是
    # **所有证据都被过滤掉 → 每个问题都拒答**，而且日志里看不出是阈值的问题。
    #
    # 0.01 的含义是"只挡掉完全无关的"：真正的质量闸门不在这里，而在
    # `filter_min_hits`（召回不足触发过滤兜底）与 `self_check`（引用缺失即拒答）。
    # 想收紧请按你自己的 embedding 分布调，不要沿用这个历史默认值。
    retrieval_min_score: float = 0.01
    context_max_chars: int = 4000  # 上下文预算：超出按分数从高到低截断

    # ---- 阶段 2：结构化过滤 + 两路融合 ----
    #: 两路融合权重（各自先归一化到 0~1 再加权）。调大 lexical 会让"字面命中"更吃香
    retrieval_vector_weight: float = 0.65
    retrieval_lexical_weight: float = 0.35
    #: 词面召回的最低关键词覆盖率：太低会把半个库捞进来
    retrieval_lexical_min_coverage: float = 0.5
    #: 过滤后命中少于此值 → 补一次不过滤的检索（防误过滤导致漏召回）
    retrieval_filter_min_hits: int = 3
    retrieval_enable_lexical: bool = True
    retrieval_enable_filter: bool = True
    #: 阶段 4 W4-3：融合策略。`weighted` = 归一化加权求和（阶段 2 行为），`rrf` = 倒数排名融合
    retrieval_fusion: str = "weighted"
    #: RRF 的平滑常数 k。越小越"偏爱头部"，60 是原论文与工业界的常用值
    retrieval_rrf_k: int = 60

    # ---------------- 阶段 4：Memory（W4-1）----------------
    #: 记忆总开关。关闭时上下文里没有 [用户偏好] 区块，行为退回阶段 3
    memory_enabled: bool = True
    #: 各层半衰期（天）。语义层用 90（设计文档 §7.5：偏好类 90）
    memory_half_life_short_term: float = 1.0
    memory_half_life_episodic: float = 30.0
    memory_half_life_semantic: float = 90.0
    #: 权重低于此值 → 归档（不物理删除）
    memory_archive_threshold: float = 0.05
    #: 每个区块最多注入几条（记忆是调味，不能盖过证据）
    memory_max_items_per_section: int = 5
    #: 每多少轮写一次会话摘要（设计文档 §7.5）
    memory_summarize_every: int = 8

    # ---------------- 阶段 4：上下文压缩（W4-3）----------------
    #: 压缩顺序固定为 证据 > 历史对话 > 记忆，关闭则改为"只截断证据"（阶段 3 行为）
    context_compress_enabled: bool = True

    # ---------------- 阶段 5：评测 ----------------
    #: LLM-as-a-Judge 忠实度评判开关。默认关闭：一次全量评测会多出与用例等量的
    #: LLM 调用，而输出只有 1 bit；需要盯生成质量时再显式打开（EVAL_LLM_JUDGE=true）。
    eval_llm_judge: bool = False

    # ---------------- 离线批处理（阶段 1）----------------
    #: 单条任务的最大重试次数（超过后不再自动重试，需人工排查）
    job_max_attempts: int = 3
    #: 单次跑批的成本上限（分）。到了就停，剩下的下次跑批继续 —— 防止一次跑批烧穿预算
    job_max_cost_cents: int = 5000

    # ---------------- 视觉模型（阶段 1 W1-2，生成 caption / ai_tags）----------------
    vision_provider: str = "auto"  # auto | openai | mock
    vision_model: str = "qwen-vl-max"
    vision_model_version: str = "qwen-vl-max@v1"
    vision_api_key: str = ""
    vision_base_url: str = ""  # 留空时复用 LLM_BASE_URL
    vision_timeout_sec: float = 60.0

    # ---------------- 可观测 ----------------
    # 相对 backend/ 解析；JSONL 便于脱离数据库直接 grep / 画 trace
    trace_dir: str = "../backend/data/traces"

    # ---------------- 演示数据 ----------------
    demo_user_name: str = "我"  # 单用户部署，阶段 0 用于演示语料归属

    # ---------------- 阶段 3+（先占位，阶段 0 不使用）----------------
    redis_url: str = "redis://localhost:6379/0"
    amap_api_key: str = ""
    amap_js_key: str = ""
    rerank_model: str = "bge-reranker-v2-m3"

    # ---------------- 阶段 3：feature flags（默认全关，PROJECT.md §二、[8]）----------------
    # 四个开关单看都很平凡，但它们是 `make test-degradation` 的定义域：
    # 全关时的行为必须与阶段 2 逐条一致。新增扩展一律照这个模式加。
    tools_enabled: bool = False
    cache_enabled: bool = False
    rerank_enabled: bool = False
    hnsw_enabled: bool = False

    # ---------------- 阶段 3：工具与地图 ----------------
    amap_base_url: str = "https://restapi.amap.com/v3"
    amap_timeout_sec: float = 6.0
    amap_max_retries: int = 1
    tool_timeout_sec: float = 8.0
    tool_max_retries: int = 1

    # ---------------- 阶段 3：缓存 ----------------
    cache_backend: str = "auto"  # auto | memory | redis | null
    cache_max_entries: int = 5000  # InMemoryCache 的 LRU 上限
    redis_socket_timeout_sec: float = 1.0

    # ---------------- 阶段 3：Rerank / HNSW ----------------
    rerank_base_url: str = ""  # 留空时复用 LLM_BASE_URL
    rerank_api_key: str = ""  # 留空时复用 LLM_API_KEY
    rerank_candidates: int = 20  # 候选上限（单次问答 rerank 只调 1 次）
    rerank_top_n: int = 8
    #: pgvector HNSW 的候选过采样倍数（官方建议量级；真实库上按验收再调）
    #: 是否允许走 HNSW 近似检索。**数据量过万之前保持 False**。
    #:
    #: 为什么小库上必须关：pgvector 的 HNSW 是"先遍历图、后套 WHERE"，
    #: 图本身在几十条文档下根本没建好，近似近邻会严重偏离真实最近邻
    #: （实测 70 条文档：精确扫描 Top1 命中且分数 0.407，走 HNSW 直接归零）。
    #: 阶段 3 建索引只是为了验证"建得起来 + 回退逻辑对"，不是为了让线上走它。
    #:
    #: 与 feature flag `hnsw_enabled` 是**两道门**：任一个为 False 都走精确扫描。
    #: 之所以留两道：flag 管"这个特性是否启用"（PROJECT.md §二、[8] 默认全关），
    #: 本字段管"数据量是否已经到了该用它的阶段"—— 后者是运维判断，不该混进特性开关。
    retrieval_use_ann: bool = False
    retrieval_hnsw_oversample: int = 4

    # ---------------- 阶段 6 扩展开关（默认全关，PROJECT.md §二、[8]）----------------
    #: W6-1 本地照片导入（EXIF 解析 + 内容指纹去重）
    photo_import_enabled: bool = False
    #: W6-4 地理模糊（后端）+ 定向加密信物（前端）
    privacy_enabled: bool = False

    # ---------------- 扩展开关（阶段 7，默认全关）----------------
    semantic_enabled: bool = False

    # 阶段 16 删除的死配置（声明了但全仓 0 引用）：
    #   map_album_enabled / native_bridge_enabled —— 前端能力，后端从未读取；
    #   zk_enabled / chain_enabled —— 从未有实现；
    #   minio_* —— 存储方案未落地；
    #   tool_rate_limit_per_min —— 宣称限流但没有任何实现；
    #   privacy_version / semantic_tag_version —— 契约版本，不该由环境变量控制，
    #     语义版本的真相源是 `utils/semantic_tags.py::SEMANTIC_TAG_VERSION`。

    # ---------------- 派生属性 ----------------
    @property
    def llm_key_present(self) -> bool:
        """是否配置了 LLM 密钥（决定 auto 走真实还是 mock）。"""
        return bool(self.llm_api_key.strip())

    @property
    def embedding_key_present(self) -> bool:
        return bool(self.embedding_api_key.strip()) or self.llm_key_present

    @property
    def vision_key_present(self) -> bool:
        return bool(self.vision_api_key.strip()) or self.llm_key_present

    @property
    def resolved_vision_base_url(self) -> str:
        return self.vision_base_url.strip() or self.llm_base_url

    @property
    def resolved_embedding_base_url(self) -> str:
        """embedding 未单独配置端点时复用 LLM 端点（多数国产平台两者同源）。"""
        return self.embedding_base_url.strip() or self.llm_base_url

    @property
    def resolved_trace_dir(self) -> Path:
        """trace 输出目录（绝对路径，自动创建）。"""
        path = (BACKEND_DIR / self.trace_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def backend_dir(self) -> Path:
        return BACKEND_DIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试要覆盖配置时用 `get_settings.cache_clear()`。"""
    return Settings()


def reload_settings() -> Settings:
    """重新读取 .env（改了配置又不想重启进程时用）。"""
    get_settings.cache_clear()
    return get_settings()
