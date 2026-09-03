"""数据源数据模型。

CommentRecord 承载从 api_job 展开后的一条评论记录。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CommentRecord:
    """从 api_job 展开的一条评论，携带所属作业、评论文本、视频上下文和初筛结果。

    字段来自 `api_job.request_payload.comments[*]` 与 `api_job.result.results[*]`
    按 comment_id 关联后的结果。
    """

    # ---- 评论主体（来自 request_payload） ----
    comment_id: str
    content: str = ""
    video_title: str = ""
    comment_author: str = ""
    comment_author_uid: str = ""
    comment_like_count: int = 0

    # ---- 初筛结果（来自 result，可能为 None） ----
    passed: bool = field(default=False)
    is_car_owner: Optional[bool] = None
    has_purchase_intent: Optional[bool] = None
    analysis: Optional[str] = None

    # ---- 所属作业（来自 api_job 行） ----
    job_id: str = ""
    job_status: str = ""
    job_created_at: Optional[datetime] = None

    def to_evidence(self) -> dict:
        """输出可追溯的证据字典（用于证据链登记）。

        key 使用约定名称，前端可据此反查来源。
        """
        return {
            "comment_id": self.comment_id,
            "content": self.content,
            "video_title": self.video_title,
            "job_id": self.job_id,
            "comment_like_count": self.comment_like_count,
            "passed": self.passed,
            "is_car_owner": self.is_car_owner,
            "has_purchase_intent": self.has_purchase_intent,
        }
