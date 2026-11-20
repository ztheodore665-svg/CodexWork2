"""离线批处理（阶段 1）。

  base.py       框架：幂等键 / 认领 / 重试 / 成本闸 / 状态回写
  caption_job   W1-2：照片 → caption + ai_tags
  index_job     W1-3：按天聚合 → chunk → embedding → Document

共同原则：**重跑不烧钱**。同一 (photo, job_type, ai_model) 只做一次。
"""
