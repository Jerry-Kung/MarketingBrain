"""Reviewer 独立评审测试（mock LLM）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from app.agent.reviewer import Reviewer, parse_verdict
from app.agent.budgets import AgentBudget, BudgetCounter
from app.agent.protocols import InvestigationCard, InvestigatorResult
from app.store.evidence import EvidenceStore


class FakeLLM:
    def __init__(self, content):
        self.content = content
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMResult, LLMUsage
        return json.loads(self.content), LLMResult(content=self.content, usage=LLMUsage(model="test"))


class TestReviewer:
    def _verdict_json(self, verdict="pass"):
        return json.dumps({"verdict": verdict, "issues": [{"type": "sample_bias", "detail": "样本偏"}],
                           "supplement_query": "补充调查" if verdict == "request_supplement" else ""})

    def test_review_pass(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        llm = FakeLLM(self._verdict_json("pass"))
        rev = Reviewer(llm, s)
        v = rev.review([InvestigatorResult("evidence_sufficient", findings=["f"])], [], EvidenceStore("t1"), "t1")
        assert v.verdict == "pass"
        assert v.supplement_query == ""

    def test_review_request_supplement(self):
        from app.core.config import Settings
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m")
        llm = FakeLLM(self._verdict_json("request_supplement"))
        rev = Reviewer(llm, s)
        v = rev.review([InvestigatorResult("data_insufficient")], [], EvidenceStore("t1"), "t1")
        assert v.verdict == "request_supplement"
        assert v.supplement_query == "补充调查"


class TestParseVerdict:
    def test_parse_pass(self):
        out = parse_verdict('{"verdict":"pass","issues":[]}')
        assert out.verdict == "pass"

    def test_parse_bare_dict(self):
        out = parse_verdict({"verdict": "request_supplement", "supplement_query": "q"})
        assert out.verdict == "request_supplement"

    def test_parse_missing_supplement_query_defaults_empty(self):
        out = parse_verdict('{"verdict":"request_supplement"}')
        assert out.supplement_query == ""
