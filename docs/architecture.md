# Marketing Brain V0.1 架构

> 系统级信息：系统边界、核心模块职责、分层、数据流、外部依赖、关键技术约束。
> 变更系统形态时更新本文件。

## 1. 系统边界

Marketing Brain V0 是一套面向汽车舆情分析的轻量受控 Agent Harness。V0.1 边界：

**V0.1 负责：**
- 通过 Docker Compose 在测试环境启动；
- 正确读取测试环境 MySQL 的真实舆情数据（`api_job` 表，约 97 万条评论）；
- 把用户的自由文本输入解析为结构化分析意图；
- 建立逻辑数据快照（记录边界，不复制数据）；
- 持久化任务、运行事件、结果（SQLite）。

**V0.1 不负责（后续版本）：**
- LLM 报告生成（V0.2）；
- 受控工作流/Skill 机制（V0.3）；
- 主子 Agent Loop（V0.4）；
- 完整审计可视化工作台（V0.5）。

## 2. 核心模块职责

| 模块 | 路径 | 职责 |
|---|---|---|
| 配置 | `app/core/config.py` | 从 `.env` 加载运行配置。数据库配置必须显式提供，缺失时清晰报错。 |
| 数据源 | `app/datasource/` | MySQL 只读适配器。`adapter.py` 暴露预定义查询方法，禁止任意 SQL。 |
| 任务理解 | `app/understanding/` | 从自由文本解析结构化意图（分析对象、时间范围、目标类型）。V0.1 用规则+关键词，V0.2 接 LLM。 |
| 逻辑快照 | `app/snapshot/` | 记录数据边界（时间窗、最大ID），保证一次运行口径一致。 |
| 状态存储 | `app/store/` | SQLite 持久化任务、事件、结果。通过 Repository 接口为 V1 换库留口。 |
| API | `app/api/` | REST 端点：健康检查、任务创建/查询、数据概览。`create_app` 工厂便于测试注入。 |

## 3. 分层与数据流

```
前端 (Vite+React)
   │  /api/* (REST + 2-3s 轮询)
   ▼
FastAPI (app/api)
   │
   ├── 任务理解 (understanding) ──► 结构化意图
   ├── 逻辑快照 (snapshot)      ──► 数据边界
   ├── SQLite 存储 (store)      ──► 任务/事件/结果
   └── 数据源 (datasource)      ──► 测试环境 MySQL（只读）
```

## 4. 外部依赖

| 依赖 | 用途 | 关键约束 |
|---|---|---|
| 测试环境 MySQL (`drive_intent_backend.api_job`) | 舆情分析原始数据 | **只读账号**（仅 SELECT）。数据约 97 万条评论。 |
| LLM API | V0.2 接入，V0.1 仅预留配置 | `.env` 配置 base_url / key / model。 |

## 5. 关键技术约束

1. **只读**：数据源账号仅授 SELECT，连接后强制只读事务；调用方无法执行任意 SQL。
2. **逻辑快照**：不复制几十万条原始数据，只记录边界，保证一次运行口径一致。
3. **任务理解优先**：输入始终为自由文本，通过意图识别模块结构化；不设"前端选择车型"模式。
4. **编码**：数据库 `utf8mb4`；Windows 终端默认非 UTF-8，脚本需 `sys.stdout.reconfigure(encoding='utf-8')`。
5. **`like` 保留字**：`api_job` 取数 SQL 中 `comment_like_count` 列需反引号转义。
6. **数据字段**：`preset_brand` / `preset_model` 为 NULL 不可用；品牌/车型/话题需从 `video_title` 提取。

## 6. 部署

- **Docker 多阶段构建**：前端 Vite 编译产物由 FastAPI 统一提供。
- **单应用服务 + 持久化卷**：`docker-compose.yml` 只启动一个 app 服务，挂载 `mb-app-data` 卷。
- **直接连接测试环境 MySQL**：不在 Compose 中复制 MySQL。
- **本地开发**：Vite dev server (5173) 代理 `/api` 到后端 (19783)；后端用 `python -m app.main`。
