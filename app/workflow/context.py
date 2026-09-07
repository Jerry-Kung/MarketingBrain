"""工作流上下文构建（Stage 专用 LLM prompt）。"""


def build_stage_context(stage, evidence_store) -> list[dict]:
    """构建 Stage 专用 LLM 上下文（system prompt + 已有证据摘要）。

    Args:
        stage: StageDefinition 实例
        evidence_store: EvidenceStore 实例

    Returns:
        messages: list[dict]，格式为 [{"role": "system", "content": "..."}]
    """
    system_prompt = stage.system_prompt

    # 追加已有证据摘要（供后续 stage 参考前序 stage 的发现）
    evidence_summary = _build_evidence_summary(evidence_store)
    if evidence_summary:
        system_prompt += f"\n\n## 已有证据\n{evidence_summary}"

    return [{"role": "system", "content": system_prompt}]


def _build_evidence_summary(evidence_store) -> str:
    """构建证据摘要（简要列出已登记的证据数量）。"""
    all_records = evidence_store.all_records()
    if not all_records:
        return ""

    comments_count = sum(1 for r in all_records if r.kind == "comment")
    videos_count = sum(1 for r in all_records if r.kind == "video")
    stats_count = sum(1 for r in all_records if r.kind == "stat")
    judgments_count = sum(1 for r in all_records if r.kind == "judgment")

    lines = []
    if comments_count:
        lines.append(f"- 评论证据: {comments_count} 条")
    if videos_count:
        lines.append(f"- 视频证据: {videos_count} 条")
    if stats_count:
        lines.append(f"- 统计证据: {stats_count} 条")
    if judgments_count:
        lines.append(f"- 结构化判断: {judgments_count} 条")

    return "\n".join(lines) if lines else ""
