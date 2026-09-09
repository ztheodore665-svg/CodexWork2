# 车路云协同管控平台 - 后端镜像（多阶段构建）
# 注意：容器需挂载 SUMO_HOME 指向的 SUMO 安装目录，或安装 sumo。

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖（sumolib 需 libxerces-c；TraCI 客户端为纯 Python）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxerces-c3.2 libproj25 libgdal32 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
