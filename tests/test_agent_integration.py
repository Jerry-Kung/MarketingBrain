"""V0.4 Agent 集成测试（mock LLM + FakeDS，跨多轮）。

验证一条真实下钻链：主管生成一张调查卡，子 Agent 依次调用
sample_comments -> drill_evidence 两个不同工具后停止，再经 reviewer 通过，
最终组装出三层报告。验证预算计数、停止原因、报告字段与工具事件序列。
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import Orchestrator
from app.store.evidence import EvidenceStore
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.understanding.intent import AnalysisIntent
from datetime import datetime


class FakeDS:
    """假数据源：只需 fetch_comments / count_comments 即可支撑下钻链。"""

    def count_comments(self, **kw):
        return 100

    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [
            CommentRecord(
                comment_id=f"c{i}", content=f"评论{i} 油耗", video_title="坦克300",
                job_id="j1", comment_like_count=10, passed=True,
                is_car_owner=True, has_purchase_intent=False,
            )
            for i in range(limit)
        ]


class FakeLLM:
    """按调用序号依次返回预设响应。"""

    def __init__(self):
        self.responses = []

    def push(self, content=None, raw=None):
        self.responses.append({"content": content, "raw": raw or {}})

    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return json.loads(nxt["content"]), LLMResult(
            content=nxt["content"], usage=LLMUsage(model="m")
        )

    def chat(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        nxt = self.responses.pop(0) if self.responses else {"content": "{}"}
        return LLMResult(
            content=nxt.get("content"), usage=LLMUsage(model="m"),
            raw=nxt.get("raw", {}),
        )


def _tool_call(tool, args):
    """构造一个带 function tool_calls 的 raw，供 FakeLLM.chat 返回。"""
    return {
        "content": None,
        "raw": {
            "choices": [{
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "x", "type": "function",
                        "function": {"name": tool, "arguments": json.dumps(args)},
                    }],
                }
            }]
        },
    }


class TestAgentIntegration:
    def test_dynamic_drill_chain(self, tmp_path):
        from app.core.config import Settings
        settings = Settings(
            DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
            LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
            AGENT_MAX_SUBTASKS=2, AGENT_MAX_LOOPS=5,
            AGENT_MAX_TOOL_CALLS=10, AGENT_MAX_SUPPLEMENTS=1,
        )
        db = str(tmp_path / "t.db")
        task_repo = TaskRepository(db)
        task_repo.init_schema()
        event_repo = EventRepository(db)
        event_repo.init_schema()
        store = EvidenceStore("t1")
        snap = LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31)
        )
        llm = FakeLLM()

        # supervisor：1 张下钻卡（建议两个不同工具）
        llm.push(json.dumps({
            "cards": [{
                "card_id": "c1", "goal_type": "drill", "title": "油耗", "objective": "下钻油耗",
                "evidence_requirements": ["样本"],
                "suggested_tools": ["sample_comments", "drill_evidence"], "priority": 1,
            }]
        }))
        # investigator 轮1：sample_comments
        llm.push(content=None, raw=_tool_call("sample_comments", {"limit": 3})["raw"])
        # investigator 轮2：drill_evidence（不同工具）
        llm.push(content=None, raw=_tool_call("drill_evidence", {"keyword": "油耗", "limit": 3})["raw"])
        # investigator 轮3：停止
        llm.push(json.dumps({
            "type": "stop", "stop_reason": "evidence_sufficient",
            "summary": "充分", "findings": ["油耗为主"],
        }))
        # reviewer：pass
        llm.push(json.dumps({"verdict": "pass", "issues": []}))

        task = task_repo.create_task(
            raw_input="分析坦克300",
            parsed_intent={"goal_type": "drill"},
            snapshot=snap.to_dict(),
        )
        orch = Orchestrator(llm, FakeDS(), snap, store, event_repo, task_repo, settings)
        result = orch.run(task.task_id, AnalysisIntent("坦克300", None, "drill"))

        # 预算：恰好两次数值型工具调用
        assert result["agent"]["budget"]["tool_calls"]["used"] == 2
        # 子任务停止原因
        assert result["agent"]["subtask_results"][0]["stop_reason"] == "evidence_sufficient"
        # 三层报告字段
        assert "themes" in result["report"]
        assert "evidence_gaps" in result["report"]
        # 工具事件序列（按执行顺序）
        tools = [
            e.payload["tool"]
            for e in event_repo.get_events(task.task_id)
            if e.event_type == "subtask_tool"
        ]
        assert tools == ["sample_comments", "drill_evidence"]
