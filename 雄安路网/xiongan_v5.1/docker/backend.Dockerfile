# 车路云平台 · 后端容器镜像
# 构建上下文 = 项目根（xiongan_v5.1），需包含 backend/ 与 networks/
#
# 注意：镜像内 SUMO 来自 Debian 软件源（版本可能低于本机 1.27）。
# 如遇 TraCI 命令不兼容，请改用 eclipse/sumo 官方镜像：
#   FROM eclipse/sumo:1.19.0  （其内自带较新 sumo，另行安装 python3 + pip）
FROM python:3.12-slim

# SUMO（提供 /usr/bin/sumo，engine 在非 Windows 下直接走 PATH，无需 SUMO_HOME）
RUN apt-get update \
    && apt-get install -y --no-install-recommends sumo \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖层（利用 Docker 缓存）
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu

# 代码 + 路网 + 模型权重
COPY backend /app/backend
COPY networks /app/networks

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
