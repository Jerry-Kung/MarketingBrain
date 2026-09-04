# Marketing Brain 部署与运行手册（V0）

> 本手册面向运维/测试人员，说明如何部署、配置、验证项目，以及如何访问前端页面。
> 只讲操作步骤，设计原理见 `docs/architecture.md` 与 `docs/design/`。

## 1. 前置条件

- 目标机器已安装 **Docker** 与 **Docker Compose v2**（部署方式为 Docker Compose）。
- 具备可访问的**测试环境 MySQL**（只读账号，仅 `SELECT` 权限），数据库与凭据由数据源方提供。
- 项目代码已获取到目标机器（或以 git 克隆 + 本地 `docker compose` 构建）。

## 2. 环境配置

本地/测试环境都从**项目根目录的 `.env` 文件**读取配置。`.env` 不在版本库中，需手工创建。

```bash
# 在项目根目录执行：复制示例模板后填写实际值
cp .env.example .env
```

`.env.example` 只含占位符，不得填写真实凭据。需要配置的关键项：

| 配置项 | 说明 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 测试环境 MySQL 连接信息（**只读账号**） |
| `APP_PORT` | 应用监听端口，默认 `19783` |
| `APP_STATE_DIR` | SQLite 状态目录；Docker 中指向持久化卷 |
| `LLM_*` | V0.2 接入，V0.1 可留空 |

> 数据库密码若含特殊字符，需做 URL-encode，详见 `docs/评论数据获取说明.md`。

## 3. 部署（测试环境）

在项目根目录执行（**勿在本地打包**，本机无 Docker）：

```bash
docker compose up -d --build   # 构建镜像并后台启动
docker compose logs -f         # 查看运行日志
```

- 首次构建会先编译前端（Node 多阶段构建），再启动 Python 后端。
- 应用状态（任务/事件/报告）写入挂载卷 `mb-app-data`，容器重建不丢失。
- 应用内置 `/api/health` 健康检查，Docker 会周期探测。

## 4. 访问前端页面

**测试环境部署后**，访问：

```
http://<主机IP>:19783/
```

前端静态产物由 FastAPI 在同源 `19783` 端口统一提供，无需单独的前端服务。

**本地开发**用 Vite dev server（前后端分跑，代理 `/api`）：

```bash
# 终端 1：后端
python -m app.main

# 终端 2：前端
cd page && npm run dev
# 访问 http://localhost:5173/
```

> 本地 Vite 默认监听 IPv6 `[::1]:5173`，用浏览器正常访问即可；用 curl 联调时需 `curl --noproxy "*" "http://[::1]:5173/..."`。

## 5. 验证项目是否正常运行

**① 数据库连通性**（只读账号与数据源链路）：

```bash
python scripts/check_db_connectivity.py
# 退出码 0=成功；1=连接失败；2=表读取失败
```

**② 应用健康检查**：

```bash
curl --noproxy "*" http://localhost:19783/api/health
# 期望 database.available=true，并返回 job_count / comment_count
```

**③ 数据概览**（页面数据概览卡片的数据来源）：

```bash
curl --noproxy "*" http://localhost:19783/api/data-overview
```

**④ 一键单元/集成测试**：

```bash
python -m pytest -v
# 40 个测试全绿（36 单元 + 4 集成）
```

**⑤ 页面冒烟**：浏览器打开前端页面，确认能看到「数据概览」卡片的真实评论数（约 81.5 万），并能发起一条自然语言舆情分析任务。

## 6. 关键 API（快速联调）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（应用 + MySQL 状态） |
| GET | `/api/data-overview` | 数据覆盖概览（作业数/评论数/时间窗） |
| POST | `/api/tasks` | 创建分析任务，body=`{"raw_input":"分析猛士M817近期的舆情变化"}` |
| GET | `/api/tasks?task_id={id}` | 查询单任务状态与解析结果 |
| GET | `/api/tasks/list` | 任务列表 |

## 7. 常见故障与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 连通性脚本退出码 1 | `.env` 缺项 / 账号 IP 白名单 / 网络不通 | 检查 `.env`、数据库网络与账号权限 |
| `/api/health` 返回 `database.available=false` | MySQL 连接失败或数据源未配置 | 检查 `.env` 与 MySQL 连通性 |
| 前端评论数显示为「—」或「数据源不可用」 | 数据源读取失败 | 先跑连通性脚本，再查 `/api/data-overview` |
| 页面打不开 / 白屏 | 静态产物未构建（本地直连 19783 时） | 本地开发改用 Vite `:5173`；生产确认 `app/static/` 有产物 |
| Windows 终端中文乱码 | 终端默认非 UTF-8 | 使用支持 UTF-8 的终端；脚本已 `reconfigure(encoding="utf-8")` |

## 8. 停止与清理

```bash
docker compose down        # 停止容器，保留数据卷
docker compose down -v     # 停止并删除数据卷（历史任务清空）
```
