# PostGIS + pgvector 单镜像（自建：官方没有两者合一的镜像）
#
# 【为什么基础镜像是 pgvector/pgvector:pg16，而不是 postgis/postgis】
# 原方案 postgis/postgis:16-3.4 基于 Debian 11(bullseye)，2026 年实测已不可行：
#   - PostgreSQL 官方源 apt.postgresql.org 对 bullseye 已 404，归档源同样 404
#   - 源里既没有 postgresql-server-dev-16，也没有 postgresql-16-pgvector
#   - 想从源码编译 pgvector 会因缺少 /usr/include/postgresql/16/server 头文件失败
# 而 pgvector/pgvector:pg16 基于 Debian 12(bookworm)：
#   - 自带 pgvector，无需编译
#   - Debian 12 的 apt 源里 postgresql-16-postgis-3 可直接安装
# 于是 PostGIS 与 pgvector 两个扩展都齐全，满足项目需求。
#
# 注意：将来换 PostgreSQL 大版本时，要同步调整包名（postgresql-16-postgis-3 → 对应版本）。

FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

# 三个扩展不在镜像里预建，交给首次 Alembic 迁移统一创建（见 alembic/versions/0001）：
#   CREATE EXTENSION IF NOT EXISTS postgis;
#   CREATE EXTENSION IF NOT EXISTS vector;
#   CREATE EXTENSION IF NOT EXISTS pg_trgm;
# 好处：扩展的创建时机与迁移版本绑定，换机器重建库不会漏。
