"""跨层共用的**策略常量**（k-匿名门禁、序列截断上限）。

## 为什么这两个数字要单独一个模块

`K_ANONYMITY` 与 `MAX_SEQUENCE_STEPS` 同时被两边需要：

- `app/schemas/semantic.py`（请求体的 `max_length`、响应里的 `k` 默认值）
- `app/services/semantic_trajectory.py`（门禁判定、截断逻辑）

放在 `schemas/` 是唯一不会造成方向错误的位置：`services → schemas` 是允许的方向，
而 `schemas → services` 不是。阶段 16 之前这两个常量住在 `services` 里，
schemas 反向 import 它，形成了包级循环（`tests/test_layering.py` 的守卫只覆盖了
tools/services/models，没覆盖 schemas，所以一直没被发现）。

## 为什么不是 `core/config.py`

它们是**契约的一部分**，不是部署配置：前端拿到的 `k` 必须与服务端判定用的数字
逐字一致。配置可以按环境改，契约不行 —— 一旦允许 `K_ANONYMITY` 由环境变量控制，
"响应里写 k=5、实际按 3 判"就会变成一种合法配置。

`core/config.py` 里曾有一个 0 引用的 `semantic_tag_version` 字段，就是这类
"以为是配置、其实是契约"的典型；阶段 16 一并删除，版本号只保留
`utils/semantic_tags.py` 一处真相源。
"""

from __future__ import annotations

#: **发布门禁**：`support < 5` 的模式一律不可见（设计文档 §9.2）。
#:
#: 为什么是 5 而不是 3 或 10：这是 k-匿名里 k 的取值，取小了匿名集不够、
#: 取大了在"单用户起步"的产品早期永远出不了第一条推荐。5 是隐私效用与可用性
#: 折中的常用量级，且与"一个人一天最多贡献几条序列"的直觉相符。
K_ANONYMITY = 5

#: **序列截断上限**：上报与查询都只保留最后 6 步（设计文档 §9.2）。
#:
#: 长序列是强指纹：六类标签里随机 20 步的组合空间是 6^20，实际上全球唯一；
#: 截断到 6 步后同一模式会被大量行程命中。截断也顺带让"前缀"数量收敛，
#: 否则 `support` 会随序列长度指数级稀释，什么都达不到门禁。
MAX_SEQUENCE_STEPS = 6
