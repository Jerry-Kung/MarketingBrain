"""REST API 路由与应用工厂。

`create_app(db_path, datasource)` 是工厂函数，便于测试注入临时 SQLite 存储
和 stub/真实数据源。数据源为 None 时，数据概览和健康检查降级（不 500）。
LLM 未配置时任务标记失败（不 500），`background=True` 时任务后台执行。
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import (
    CreateTaskRequest,
    DataOverviewResponse,
    EvidenceListResponse,
    HealthResponse,
    ReportResponse,
    TaskListResponse,
    TaskResponse,
    TimelineEventItem,
    TimelineResponse,
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
        skill_name=task.skill_name,
        created_at=task.created_at,
        updated_at=task.updated_at,
        result=task.result,
        error=task.error,
    )


def _select_skill(goal_type: str) -> str:
    """根据意图目标类型选择 Skill（V0.3 简化映射）。"""
    # V0.3 目前只有一个 Skill，直接返回默认
    return "opinion-pulse"


def create_app(
    db_path: Optional[str] = None,
    datasource=None,
    settings: Optional[Settings] = None,
    static_dir: Optional[str] = None,
    *,
    llm_provider=None,
    background: bool = True,
    settings_override: Optional[Settings] = None,
) -> FastAPI:
    """创建 FastAPI 应用工厂。

    参数:
        db_path: SQLite 应用状态库路径。None 时用 settings.APP_STATE_DIR。
        datasource: 数据源适配器。None 时健康检查/概览降级（无真实 DB）。
        settings: 配置。None 时自动加载。
        static_dir: 前端构建产物目录。None 时用 app/static（Docker 多阶段构建放入）。
        llm_provider: LLM Provider（测试注入 mock）。None 时尝试按配置构建，
            仍无配置则任务标记失败（不 500）。
        background: True 后台线程执行任务；False 同步执行（便于测试）。
        settings_override: settings 的别名，兼容测试用法（两者取其一，优先 settings_override）。
    """
    settings = settings_override or settings or load_settings()

    # 注入 LLM Provider：外部（测试）传参优先，否则用配置构建。
    # 配置缺失（无 LLM_API_BASE/KEY/MODEL）时保持 None，不在此处抛错，
    # 由 POST /api/tasks 将任务标记为 failed。
    if llm_provider is None:
        if settings.LLM_API_BASE and settings.LLM_API_KEY and settings.LLM_MODEL:
            from app.llm.provider import LLMProvider
            llm_provider = LLMProvider.from_settings(settings)

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

    if static_dir is None:
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

    @app.get("/", include_in_schema=False)
    def index():
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
        """创建分析任务（V0.3 支持 Skill 工作流）。"""
        # 任务理解/意图识别
        intent = parser.parse(req.raw_input)
        # Controller 裁定 P5：无时间短语时默认最近 30 天窗口（与「近期」约定一致）
        if intent.time_range is None:
            from app.understanding.intent import TimeRange
            today = datetime.now().date()
            intent.time_range = TimeRange(today - timedelta(days=30), today)
        # 逻辑快照
        from app.snapshot.snapshot import LogicalSnapshot
        snap = LogicalSnapshot.from_intent(intent)
        # Controller 裁定 P10：对象按 #标签 匹配，存进快照 extra
        #（from_intent 只写 extra["object"]，不写 video_tags）
        if intent.object:
            snap.extra = dict(snap.extra or {})
            snap.extra.setdefault("video_tags", [intent.object])

        # 根据配置和执行模式决定是否启用 V0.3 工作流引擎
        # background=False（同步测试模式）保持 V0.2 基线路径，不走工作流
        use_workflow = settings.ENABLE_WORKFLOW_ENGINE and background

        # 选择 Skill（V0.3 简化映射：goal_type -> skill_name）
        skill_name = _select_skill(intent.goal_type) if use_workflow else None

        # Controller 裁定 P2：LLM 未配置时不运行、不 500，直接标记失败
        if llm_provider is None:
            err = "LLM 未配置（缺少 LLM_API_BASE/LLM_API_KEY/LLM_MODEL）"
            task = task_repo.create_task(
                raw_input=req.raw_input,
                parsed_intent=intent.to_dict(),
                snapshot=snap.to_dict(),
                skill_name=skill_name,
            )
            event_repo.append_event(
                task.task_id, "task_created",
                {"raw_input": req.raw_input, "intent": intent.to_dict()},
            )
            task_repo.mark_failed(task.task_id, err)
            event_repo.append_event(
                task.task_id, "task_failed",
                {"error": err, "raw_input": req.raw_input},
            )
            return _task_to_response(task_repo.get_task(task.task_id))

        task = task_repo.create_task(
            raw_input=req.raw_input,
            parsed_intent=intent.to_dict(),
            snapshot=snap.to_dict(),
            skill_name=skill_name,
        )
        # 记录任务创建事件
        event_repo.append_event(
            task.task_id, "task_created",
            {"raw_input": req.raw_input, "intent": intent.to_dict()},
        )

        # 数据源未配置（datasource=None）：流水线无法取数，接受任务但不执行，
        # 状态保持 pending（与健康检查/概览的降级语义一致，不 500、不启动后台线程）。
        if _ds is None:
            return _task_to_response(task)

        if use_workflow:
            # V0.3 工作流引擎后台执行
            from app.workflow.runner import start_workflow_background
            start_workflow_background(
                task.task_id,
                skill_name,
                task_repo=task_repo,
                event_repo=event_repo,
                datasource=_ds,
                llm_provider=llm_provider,
                settings=settings,
            )
        elif background:
            # V0.2 基线流水线后台执行（保留不变）
            from app.pipeline.runner import start_task_background
            start_task_background(
                task_id=task.task_id, task_repo=task_repo, event_repo=event_repo,
                datasource=_ds, llm_provider=llm_provider, settings=settings,
            )
        else:
            # V0.2 基线流水线同步执行（background=False，测试专用）
            from app.pipeline.runner import run_task_sync
            run_task_sync(
                task.task_id, task_repo=task_repo, event_repo=event_repo,
                datasource=_ds, llm_provider=llm_provider, settings=settings,
            )
            task = task_repo.get_task(task.task_id)
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

    @app.get("/api/tasks/{task_id}/report", response_model=ReportResponse)
    def get_report(task_id: str):
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        if task.status != "success" or not task.result:
            raise HTTPException(status_code=409,
                                detail=f"task {task_id} 未成功完成 (status={task.status})")
        return ReportResponse(
            task_id=task_id, status=task.status,
            report=task.result.get("report"),
            validation=task.result.get("validation"),
            meta=task.result.get("meta"),
            error=None,
        )

    @app.get("/api/tasks/{task_id}/evidence", response_model=EvidenceListResponse)
    def get_evidence(task_id: str):
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
        evs = []
        if task.result:
            evs = task.result.get("evidence", [])
        return EvidenceListResponse(task_id=task_id, evidence=evs)

    @app.get("/api/tasks/{task_id}/timeline", response_model=TimelineResponse)
    def get_timeline(task_id: str):
        """获取任务执行时间线（stage 与工具调用过程）。"""
        task = task_repo.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        events = event_repo.get_events(task_id)
        timeline_events = [
            TimelineEventItem(
                event_type=ev.event_type,
                payload=ev.payload,
                seq=ev.seq,
                created_at=ev.created_at,
            )
            for ev in events
        ]

        return TimelineResponse(task_id=task_id, events=timeline_events)

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

    # 若存在前端构建产物（Docker 部署时由多阶段构建放入 app/static），
    # 则挂载到根路径：Vite 产物中的资源引用为 /assets/*、/favicon.svg 等根路径。
    # 必须在所有 API 路由注册之后挂载，根路径 mount 只兜底未匹配的请求。
    # 本地开发时用 Vite dev server，此目录通常不存在，静默跳过。
    if os.path.isdir(static_dir):
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

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
