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

# 系统依赖：全部二进制依赖（pydantic-core/uvloop/httptools/watchfiles 等）
# 在 python:3.14-slim 上均有 cp314 预编译 wheel，无需 apt 安装 gcc 编译，
# 也无需额外系统库（PyMySQL 为纯 Python 实现）。故不执行 apt-get。
WORKDIR /app

# 先拷贝依赖声明以利用 docker layer 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

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

EXPOSE 19783

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('APP_PORT','19783'); urllib.request.urlopen(f'http://localhost:{port}/api/health', timeout=4)"

# 启动应用（绑定 0.0.0.0，端口取自 APP_PORT 环境变量）
CMD ["python", "-m", "app.main"]
