"""路由层的公共依赖 / 守卫。

## 为什么把「开关未开 → 404」收成一处

阶段 16 之前，同一段三行样板在 5 个路由文件里抄了 12 遍：

```python
if not is_enabled("photo_import"):
    raise HTTPException(status_code=404, detail="该能力未启用")
```

抄 12 遍的问题不是啰嗦，是**语义会漂**：只要有一处写成 403，前端就会看到
"这个接口存在但你没权限"，而实际语义是"这个能力根本没开"（详见
`api/v1/trips.py` 模块 docstring 里 404 vs 403 的取舍）。
收成一个函数后，`grep "该能力未启用"` 应该只剩本文件这一处。

## 为什么是普通函数而不是 FastAPI 的 `Depends`

两种写法都能消样板。选函数是因为它不改变 handler 的签名 ——
`Depends` 会在每个 handler 上多一个用不到的参数，而现有测试里有直接调用
handler 的用例（不经 HTTP），改签名会让它们全部要跟着改。
守卫的位置（body 校验之后、业务之前）在两种写法里是一样的。
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.feature_flags import is_enabled


def ensure_flag(name: str) -> None:
    """能力开关未开时抛 404（"能力不存在"，不是"无权限"）。

    未知开关名会由 `is_enabled` 抛 `ConfigError` —— 打错字时当场炸，
    而不是静默地永远 404。
    """
    if not is_enabled(name):
        raise HTTPException(status_code=404, detail="该能力未启用")
