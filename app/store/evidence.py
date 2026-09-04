"""本次运行的证据索引。

登记流水线取证阶段命中的 comment_id / job_id / 统计，供引用校验与
前端「结论→证据」回查。V0.2 证据在单次运行内维护并随任务结果持久化。
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str                 # comment / video / stat
    comment_id: Optional[str] = None
    job_id: Optional[str] = None
    content: str = ""
    video_title: str = ""
    like_count: int = 0
    passed: Optional[bool] = None
    is_car_owner: Optional[bool] = None
    has_purchase_intent: Optional[bool] = None
    source: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "comment_id": self.comment_id,
            "job_id": self.job_id,
            "content": self.content,
            "video_title": self.video_title,
            "like_count": self.like_count,
            "passed": self.passed,
            "is_car_owner": self.is_car_owner,
            "has_purchase_intent": self.has_purchase_intent,
            "source": self.source,
            "extra": self.extra,
        }


class EvidenceStore:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._comments: dict[str, EvidenceRecord] = {}
        self._videos: dict[str, EvidenceRecord] = {}
        self._stats: list[EvidenceRecord] = []

    def register_comment(self, record, source: str) -> EvidenceRecord:
        comment_id_str = str(record.comment_id)

        # 幂等：如果已登记，返回现有记录并更新源列表
        if comment_id_str in self._comments:
            ev = self._comments[comment_id_str]
            if "sources" not in ev.extra:
                ev.extra["sources"] = [ev.source]
            if source not in ev.extra["sources"]:
                ev.extra["sources"].append(source)
            return ev

        # 新登记
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="comment",
            comment_id=comment_id_str,
            job_id=str(record.job_id) if record.job_id else None,
            content=record.content or "",
            video_title=record.video_title or "",
            like_count=int(record.comment_like_count or 0),
            passed=record.passed,
            is_car_owner=record.is_car_owner,
            has_purchase_intent=record.has_purchase_intent,
            source=source,
            extra={"analysis": record.analysis, "sources": [source]},
        )
        if ev.comment_id:
            self._comments[ev.comment_id] = ev
        return ev

    def register_video(self, *, job_id, video_title, comment_count,
                       source: str) -> EvidenceRecord:
        job_id_str = str(job_id)

        # 幂等：如果已登记，返回现有记录并更新源列表
        if job_id_str in self._videos:
            ev = self._videos[job_id_str]
            if "sources" not in ev.extra:
                ev.extra["sources"] = [ev.source]
            if source not in ev.extra["sources"]:
                ev.extra["sources"].append(source)
            return ev

        # 新登记
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="video",
            job_id=job_id_str,
            video_title=video_title or "",
            like_count=0,
            source=source,
            extra={"comment_count": int(comment_count), "sources": [source]},
        )
        if ev.job_id:
            self._videos[ev.job_id] = ev
        return ev

    def register_stat(self, *, label, detail, count, source: str) -> EvidenceRecord:
        ev = EvidenceRecord(
            evidence_id=_new_id(),
            kind="stat",
            source=source,
            extra={"label": label, "detail": detail, "count": int(count)},
        )
        self._stats.append(ev)
        return ev

    def all_records(self) -> list[EvidenceRecord]:
        return list(self._comments.values()) + list(self._videos.values()) + list(self._stats)

    def to_dicts(self) -> list[dict]:
        return [r.to_dict() for r in self.all_records()]

    def has_comment(self, comment_id) -> bool:
        return str(comment_id) in self._comments

    def has_video(self, job_id) -> bool:
        return str(job_id) in self._videos
