# ==========================================================
# Marketing Brain - 多阶段构建
# 阶段1: 编译 TypeScript/React 前端 -> 静态产物
# 阶段2: Python 后端运行镜像（含前端静态产物由 FastAPI 统一提供）
# ==========================================================

# ---------- 阶段1: 前端构建 ----------
FROM node:24-slim AS frontend-build
WORKDIR /build/page
COPY page/package*.json ./
RUN npm ci --no-audit --no-fund
COPY page/ ./
RUN npm run build

# ---------- 阶段2: 后端运行 ----------
FROM python:3.14-slim AS runtime

# 系统依赖（pymysql 需要 libmysqlclient 或用纯 python，二者皆不需额外编译；
# 为保证 utf8mb4 与国内网络稳定，仅安装最小依赖即可）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷贝依赖声明以利用 docker layer 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
COPY app/ ./app/
COPY docs/ ./docs/
COPY scripts/ ./scripts/

# 拷贝前端构建产物到 FastAPI 静态目录
# （由 app.api.routes 挂载 / 提供 index.html）
COPY --from=frontend-build /build/page/dist/ ./app/static/

# 应用状态持久化目录
RUN mkdir -p /data/state
ENV APP_STATE_DIR=/data/state

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"

# 启动应用（绑定 0.0.0.0）
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
