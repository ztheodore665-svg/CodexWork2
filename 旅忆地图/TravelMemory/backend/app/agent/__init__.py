"""Agent 运行时：pipeline（阶段 0~3）/ graph（阶段 4+）。

子模块按需显式导入，避免 import app.agent 就把 LLM/Embedding 客户端拉起来。
"""
