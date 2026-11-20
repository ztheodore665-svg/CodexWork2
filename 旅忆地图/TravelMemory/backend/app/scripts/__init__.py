"""命令行脚本（不是服务模块，直接 python -m 运行）。

刻意不在包初始化里 import 子模块：那会让 `python -m app.scripts.xxx` 触发重复导入警告。
"""
