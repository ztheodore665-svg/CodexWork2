"""统一日志。

为什么自己配：CLI 与后续 FastAPI 共用一套格式，trace JSONL 之外的日志必须
能直接看出「哪个模块 / 哪次 Run」。日志里禁止打印原文内容之外的隐私字段。
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """配置根 logger。重复调用安全。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)-28s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # httpx 每次请求都打 INFO，太吵
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
