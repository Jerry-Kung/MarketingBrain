"""API 请求/响应模型（Pydantic）。"""

from typing import Optional

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    """创建分析任务的请求。核心是自由文本输入。"""

    raw_input: str = Field(
        min_length=1,
        description="用户的自然语言分析问题，如：分析坦克300近期的舆情变化",
    )
    # 可选：任务级别参数（V0.1 预留）
    max_subtasks: Optional[int] = Field(default=None, ge=1, description="最大子任务数")
    max_tool_calls: Optional[int] = Field(default=None, ge=1, description="最大工具调用数")


class TaskResponse(BaseModel):
    """任务创建/状态查询的响应。"""

    task_id: str
    status: str
    raw_input: str
    parsed_intent: dict
    snapshot: dict
    skill_name: Optional[str] = None  # V0.3 Skill 名称
    created_at: str
    updated_at: str
    result: Optional[dict] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    app: str
    database: dict


class DataOverviewResponse(BaseModel):
    """数据覆盖概览响应。"""

    available: bool
    datasource: Optional[str] = None
    job_count: Optional[int] = None
    comment_count: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    message: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应。"""

    tasks: list[TaskResponse]


class ReportResponse(BaseModel):
    """任务报告响应。"""

    task_id: str
    status: str
    report: Optional[dict] = None
    validation: Optional[dict] = None
    meta: Optional[dict] = None
    error: Optional[str] = None


class EvidenceItem(BaseModel):
    """单条证据（评论/视频/统计）。"""

    evidence_id: str
    kind: str
    comment_id: Optional[str] = None
    job_id: Optional[str] = None
    content: str = ""
    video_title: str = ""
    like_count: int = 0
    passed: Optional[bool] = None
    is_car_owner: Optional[bool] = None
    has_purchase_intent: Optional[bool] = None
    source: str = ""
    extra: dict = {}


class EvidenceListResponse(BaseModel):
    """证据列表响应。"""

    task_id: str
    evidence: list[EvidenceItem]


class TimelineEventItem(BaseModel):
    """Timeline 事件项。"""

    event_type: str
    payload: dict
    seq: int
    created_at: str


class TimelineResponse(BaseModel):
    """任务 Timeline 响应。"""

    task_id: str
    events: list[TimelineEventItem]
