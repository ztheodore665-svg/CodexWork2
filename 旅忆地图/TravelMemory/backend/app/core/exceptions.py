"""异常体系。

分层原则：
  - 可观测模块与节点只抛本文件定义的异常，业务代码不抛裸 Exception
  - 旁路模块（隐私 / ZK / 链，阶段 7）失败抛 DegradedException，**绝不抛普通异常**，
    由调用方降级处理 —— PROJECT.md §二、[8]
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务异常的基类。"""

    #: 面向用户的中文说明；不为空时可直接展示给用户
    user_message: str = "内部错误，请稍后重试"


class ConfigError(AppError):
    """配置/环境自检失败（如扩展缺失、向量维度不一致）。"""

    user_message = "配置或数据库状态不一致，请查看终端提示"


class LLMError(AppError):
    """LLM 调用失败。

    retryable=True 表示属于网络/限流类可重试错误。
    """

    user_message = "大模型调用失败，请稍后重试"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMBudgetExceeded(LLMError):
    """当日 LLM 预算耗尽（成本硬上限，见设计文档 §13.1）。"""

    user_message = "今日大模型预算已用尽"

    def __init__(self, message: str = "LLM 日预算耗尽") -> None:
        super().__init__(message, retryable=False)


class EmbeddingError(AppError):
    """embedding 生成失败。"""

    user_message = "向量生成失败，请稍后重试"


class RetrievalError(AppError):
    """检索层失败（如向量维度不匹配、数据库不可用）。"""

    user_message = "检索服务暂时不可用"


class DegradedException(AppError):
    """旁路模块降级：功能不可用但主流程必须继续。"""

    user_message = "该增强功能暂不可用，已按默认方式处理"
