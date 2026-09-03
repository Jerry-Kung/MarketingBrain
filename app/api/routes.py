"""REST API 路由与应用工厂。

`create_app(db_path, datasource)` 是工厂函数，便于测试注入临时 SQLite 存储
和 stub/真实数据源。数据源为 None 时，数据概览和健康检查降级（不 500）。
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import (
    CreateTaskRequest,
    DataOverviewResponse,
    HealthResponse,
    TaskListResponse,
    TaskResponse,
)
from app.core.config import Settings, load_settings
from app.store.repository import EventRepository, TaskRepository
from app.understanding.intent import IntentParser


def _task_to_response(task) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        status=task.status,
        raw_input=task.raw_input,
        parsed_intent=task.parsed_intent,
        snapshot=task.snapshot,
        created_at=task.created_at,
        updated_at=task.updated_at,
        result=task.result,
        error=task.error,
    )


def create_app(
    db_path: Optional[str] = None,
    datasource=None,
    settings: Optional[Settings] = None,
) -> FastAPI:
    """创建 FastAPI 应用工厂。

    参数:
        db_path: SQLite 应用状态库路径。None 时用 settings.APP_STATE_DIR。
        datasource: 数据源适配器。None 时健康检查/概览降级（无真实 DB）。
        settings: 配置。None 时自动加载。
    """
    settings = settings or load_settings()
    if db_path is None:
        if not os.path.isabs(settings.APP_STATE_DIR):
            # 相对路径基于项目根
            state_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                settings.APP_STATE_DIR,
            )
        else:
            state_dir = settings.APP_STATE_DIR
        os.makedirs(state_dir, exist_ok=True)
        db_path = os.path.join(state_dir, "app_state.db")

    task_repo = TaskRepository(db_path=db_path)
    task_repo.init_schema()
    event_repo = EventRepository(db_path=db_path)
    event_repo.init_schema()

    parser = IntentParser()
    _ds = datasource

    app = FastAPI(title="Marketing Brain API", version="0.1.0")

    # V0 前后端本地/跨域；生产由同源代理，此处允许 Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 若存在前端构建产物（Docker 部署时由多阶段构建放入 app/static），
    # 则挂载为静态目录，实现前后端同源由 FastAPI 统一提供。
    # 本地开发时用 Vite dev server，此目录通常不存在，静默跳过。
    try:
        from fastapi.staticfiles import StaticFiles

        static_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "static"
        )
        if os.path.isdir(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")
    except Exception:
        pass

    @app.get("/", include_in_schema=False)
    def index():
        static_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "static"
        )
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            from fastapi.responses import FileResponse
            return FileResponse(index_path)
        return {"message": "Marketing Brain API。前端静态产物未构建。"}

    @app.get("/api/health", response_model=HealthResponse)
    def health():
        db_status = "ok"
        db_info = {"available": True, "datasource": "api_job"}
        if _ds is None:
            db_status = "degraded"
            db_info = {"available": False, "datasource": None}
        else:
            try:
                overview = _ds.data_overview()
                db_info = {
                    "available": True,
                    "datasource": "api_job",
                    "job_count": overview.job_count,
                    "comment_count": overview.comment_count,
                }
            except Exception as e:
                db_status = "degraded"
                db_info = {"available": False, "datasource": None, "error": str(e)}

        return HealthResponse(status="ok", app="marketing-brain", database=db_info)

    @app.post("/api/tasks", response_model=TaskResponse)
    def create_task(req: CreateTaskRequest):
        # 任务理解/意图识别
        intent = parser.parse(req.raw_input)
        # 逻辑快照
        from app.snapshot.snapshot import LogicalSnapshot
        snap = LogicalSnapshot.from_intent(intent)

        task = task_repo.create_task(
            raw_input=req.raw_input,
            parsed_intent=intent.to_dict(),
            snapshot=snap.to_dict(),
        )
        # 记录任务创建事件
        event_repo.append_event(
            task.task_id, "task_created",
            {"raw_input": req.raw_input, "intent": intent.to_dict()},
        )
        return _task_to_response(task)

    @app.get("/api/tasks", response_model=TaskResponse)
    def get_task(task_id: str):
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        return _task_to_response(task)

    @app.get("/api/tasks/list", response_model=TaskListResponse)
    def list_tasks(limit: int = 50):
        tasks = task_repo.list_tasks(limit=limit)
        return TaskListResponse(tasks=[_task_to_response(t) for t in tasks])

    @app.get("/api/data-overview", response_model=DataOverviewResponse)
    def data_overview():
        if _ds is None:
            return DataOverviewResponse(
                available=False,
                message="数据源未配置（datasource=None），无法获取概览",
            )
        try:
            ov = _ds.data_overview()
            return DataOverviewResponse(
                available=True,
                datasource="api_job",
                job_count=ov.job_count,
                comment_count=ov.comment_count,
                start_time=ov.start_time.isoformat() if ov.start_time else None,
                end_time=ov.end_time.isoformat() if ov.end_time else None,
            )
        except Exception as e:
            return DataOverviewResponse(
                available=False,
                message=f"数据源读取失败: {e}",
            )

    return app


# 供 uvicorn 直接运行：python -m app.main
def create_app_from_settings() -> FastAPI:
    """基于真实配置创建应用（用于生产入口）。"""
    settings = load_settings()
    from app.datasource.adapter import MySqlDataSource
    from sqlalchemy import create_engine

    engine = create_engine(
        settings.db_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=3,
        max_overflow=2,
    )
    ds = MySqlDataSource(engine)
    return create_app(db_path=None, datasource=ds, settings=settings)


__all__ = ["create_app", "create_app_from_settings"]
