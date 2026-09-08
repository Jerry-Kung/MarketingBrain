"""Reviewer 独立评审角色。

检查证据充分性、反例、样本偏差、结论越界。输出 ReviewVerdict：
- pass：调查通过
- request_supplement：要求一次受控补充调查（补查额度由 orchestrator 控制，至多 1 次）
Reviewer 不调用任何分析工具，只做判断与决策。
"""
import json

from app.agent.protocols import ReviewVerdict


class Reviewer:
    """独立评审。"""

    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings

    def review(self, subtask_results, cards, evidence_store, task_id) -> ReviewVerdict:
        """评估调查产出，决定通过或要求补查。

        Args:
            subtask_results: list[InvestigatorResult]
            cards: list[InvestigationCard]
            evidence_store: EvidenceStore
            task_id: str

        Returns:
            ReviewVerdict
        """
        prompt = self._build_prompt(subtask_results, cards, evidence_store)
        data, _ = self.llm_provider.chat_json(
            prompt, temperature=0.0, max_tokens=3000, timeout=self.settings.agent_llm_timeout,
        )
        return parse_verdict(data)

    def _build_prompt(self, subtask_results, cards, evidence_store) -> list[dict]:
        result_lines = [
            f"- [{r.stop_reason}] {r.summary or '(无摘要)'}；工具调用 {r.tool_calls_used} 次；发现 {r.findings}"
            for r in subtask_results
        ]
        card_lines = [c.to_dict() for c in cards]
        evidence_count = len(evidence_store.all_records())
        system = (
            "你是独立的评审角色（Reviewer）。你负责检查以下舆情调查的证据是否充分、"
            "是否有反例、样本是否存在偏差、结论是否越界。\n"
            "你不调用任何工具。\n"
            "输出必须是 JSON：{\"verdict\":\"pass\" 或 \"request_supplement\","
            "\"issues\":[{\"type\":\"evidence_gap|counter_example|sample_bias|overclaim\",\"detail\":\"...\"}],"
            "\"supplement_query\":\"...\"（verdict 为 request_supplement 时必填，用于指导一次受控补查）}。"
        )
        user = (
            "调查卡：\n" + json.dumps(card_lines, ensure_ascii=False, indent=2) + "\n"
            "各卡调查结果：\n" + "\n".join(result_lines) + "\n"
            f"已登记证据数：{evidence_count}\n"
            "请给出评审结论。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_verdict(text_or_dict) -> ReviewVerdict:
    """容错解析评审结论。"""
    if isinstance(text_or_dict, str):
        try:
            data = json.loads(text_or_dict)
        except (json.JSONDecodeError, TypeError):
            data = {}
    else:
        data = text_or_dict or {}
    verdict = data.get("verdict")
    if verdict not in ("pass", "request_supplement"):
        verdict = "pass"
    return ReviewVerdict(
        verdict=verdict,
        issues=data.get("issues", []),
        supplement_query=data.get("supplement_query", ""),
    )
