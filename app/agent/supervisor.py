"""Supervisor 角色：把任务意图拆解为结构化调查卡。

主 Agent 依据意图与既有证据，调用 LLM 生成 InvestigationCard[]。不调用任何分析工具；
工具白名单由 __init__ 注入（默认 TOOL_WHITELIST），越界建议工具在解析时剔除。
"""
import json

from app.agent.protocols import InvestigationCard
from app.agent.tools_spec import TOOL_WHITELIST


class Supervisor:
    """主 Agent：规划调查卡。"""

    def __init__(self, llm_provider, settings, tool_whitelist=None):
        self.llm_provider = llm_provider
        self.settings = settings
        self.tool_whitelist = tool_whitelist or TOOL_WHITELIST

    def plan(self, intent, evidence_store, max_subtasks: int) -> list[InvestigationCard]:
        """依据意图与既有证据生成调查卡。

        Args:
            intent: AnalysisIntent
            evidence_store: EvidenceStore（提供既有证据摘要与对象/时间范围）
            max_subtasks: 允许的最大子任务数（请求级或配置），已由调用方钳制

        Returns:
            list[InvestigationCard]
        """
        limit = max(1, min(int(max_subtasks), self.settings.AGENT_MAX_SUBTASKS))
        prompt = self._build_prompt(intent, evidence_store, limit)
        data, _ = self.llm_provider.chat_json(
            prompt, temperature=0.2, max_tokens=4000, timeout=self.settings.agent_llm_timeout,
        )
        cards = parse_cards(json.dumps(data))
        return cards[:limit]

    def _build_prompt(self, intent, evidence_store, limit) -> list[dict]:
        """构建 Supervisor 专用 prompt。"""
        obj = getattr(intent, "object", "") or ""
        goal = getattr(intent, "goal_type", "pulse") or "pulse"
        tr = getattr(intent, "time_range", None)
        time_desc = f"{tr.start} ~ {tr.end}" if tr else "不限"
        evidence_count = len(evidence_store.all_records())
        tool_list = ", ".join(sorted(self.tool_whitelist))
        system = (
            "你是营销舆情分析的主 Agent（Supervisor）。你负责把用户的调查目标拆解为"
            "若干张结构化调查卡（InvestigationCard），交给子 Agent 分别调查。\n"
            "你只做规划，不调用任何数据工具。\n"
            "每张调查卡必须包含：card_id, goal_type, title, objective, "
            "evidence_requirements, suggested_tools, priority。\n"
            "suggested_tools 只能从以下白名单中选择（可留空表示由子 Agent 自主决定）："
            f"{tool_list}。\n"
            "priority 必须是 1~5 的整数（数值越大优先级越高）。\n"
            f"最多输出 {limit} 张调查卡。输出必须是 JSON：{{'cards': [ ... ]}}。"
        )
        user = (
            f"分析对象：{obj}\n"
            f"目标类型：{goal}\n"
            f"时间范围：{time_desc}\n"
            f"已有证据登记数：{evidence_count}\n"
            "请生成聚焦、互不重叠、每个都能独立回答一个具体问题的调查卡。"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_cards(text_or_dict) -> list[InvestigationCard]:
    """从 LLM 输出解析调查卡列表（容错）。

    Args:
        text_or_dict: LLM 返回的 content 字符串或已解析的 dict。

    Returns:
        list[InvestigationCard]
    """
    if isinstance(text_or_dict, str):
        try:
            data = json.loads(text_or_dict)
        except (json.JSONDecodeError, TypeError):
            data = {}
    else:
        data = text_or_dict or {}
    if isinstance(data, list):
        raw_cards = data
    elif isinstance(data, dict):
        raw_cards = data.get("cards") or []
    else:
        raw_cards = []
    out = []
    for c in raw_cards:
        if not isinstance(c, dict):
            continue
        card = InvestigationCard.from_dict(c)
        # 越界建议工具剔除：suggested_tools 只保留 TOOL_WHITELIST 内的工具。
        card.suggested_tools = [t for t in card.suggested_tools if t in TOOL_WHITELIST]
        out.append(card)
    return out
