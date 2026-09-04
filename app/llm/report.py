"""报告生成：把结构化中间结果交给 LLM 生成「舆情策略包」。

V0.2 用一次 LLM 调用归纳主题并产生完整策略包。输出是固定结构的 JSON，
引用的证据 ID（comment_id / job_id）必须来自喂给模型的 bundle，否则会被
下游的引用校验环节剔除。
"""
import json

REPORT_SYSTEM_PROMPT = (
    "你是一名汽车舆情分析师。基于用户提供的结构化统计数据与抽样评论，"
    "生成一份《舆情策略包》。"
    "要求：\n"
    "1. 事实与判断分离：数字结论必须引用给定统计数据；解释性判断需说明依据；"
    "   证据不足以支撑的结论要标记为【待验证假设】。\n"
    "2. 只能引用输入中出现的 comment_id 与 job_id（或 video_title），"
    "   不得编造输入中不存在的 ID。\n"
    "3. 输出严格为 JSON 对象，字段如下：\n"
    "   scope(数据覆盖范围与样本说明),\n"
    "   overall(总体声量及变化),\n"
    "   themes[{theme, heat_up, refs:[comment_id]}],\n"
    "   sources[{job_id, video_title, comment_count}],\n"
    "   risk_opportunity[{type: risk|opportunity, title, reason, refs}],\n"
    "   evidence_gaps[{gap, why}],\n"
    "   assumptions[{assumption, how_to_verify}],\n"
    "   actions[{action, priority, target}],\n"
    "   metrics[{metric, target}]\n"
)


def build_report_messages(bundle: "AnalysisBundle", intent: dict) -> list[dict]:
    """构造喂给 LLM 的 [system, user] 消息列表。

    intent 为 dict（含 object/goal_type/time_range/raw_text 等），由调用方
    （task runner）解析后透传，缺省为空 dict。

    user 消息内容用 json.dumps(..., ensure_ascii=False, default=str) 序列化，
    包含 intent/scope/overall/themes/sources/samples 全量字段；stats 做了裁剪，
    每项只保留 name/result/sample_size/bias_note，丢弃 params/evidence_ids/
    time_range —— 这些字段对模型生成策略包没有直接帮助，只会增加 token 消耗。
    """
    trimmed_stats = [
        {
            "name": s.get("name"),
            "result": s.get("result"),
            "sample_size": s.get("sample_size"),
            "bias_note": s.get("bias_note"),
        }
        for s in bundle.stats
    ]
    ctx = {
        "intent": intent,
        "scope": bundle.scope,
        "overall": bundle.overall,
        "themes": bundle.themes,
        "sources": bundle.sources,
        "samples": bundle.samples,
        "stats": trimmed_stats,
    }
    user = (
        "以下是本次舆情分析的结构化统计与抽样评论，请据此生成舆情策略包：\n\n"
        + json.dumps(ctx, ensure_ascii=False, default=str)
    )
    return [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def generate_strategy_pack(bundle, provider, intent=None):
    """调用 LLM 生成策略包。返回 (report_dict, llm_result)。

    intent 为解析后的意图 dict（object/goal_type/time_range/raw_text），
    缺省透传空 dict。
    """
    messages = build_report_messages(bundle, intent or {})
    data, result = provider.chat_json(messages, max_tokens=4000)
    return data, result
