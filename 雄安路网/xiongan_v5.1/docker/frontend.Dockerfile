# 车路云平台 · 前端容器镜像（构建静态产物 + nginx 托管并反代后端）
# 构建上下文 = 项目根（xiongan_v5.1）

# ── 构建阶段 ──
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend ./
RUN npm run build

# ── 运行阶段：nginx 托管 dist，并把 /api /ws 反代到后端容器 ──
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
