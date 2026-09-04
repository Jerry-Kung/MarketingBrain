"""报告引用校验。

从报告 JSON 中提取引用的 comment_id / job_id（video_id），逐一到本次运行的
EvidenceStore 中核对。合法引用保留；非法引用剔除并记录到 validation.rejected_refs。

设计约束：LLM 只能引用喂给它的证据；输出中出现的未知 ID 直接剔除，
不允许伪造证据混入最终报告。
"""


def _collect_cited_ids(obj) -> set:
    """深度遍历 report，收集所有被引用到的 ID（去重）。

    覆盖三处来源：
    - 任意名为 refs 的列表（如 themes[].refs、risk_opportunity[].refs）
    - 任意名为 job_id 的字段（如 sources[].job_id）
    - 任意名为 comment_id 的字段（正常结构外混入的孤立引用）
    """
    found = set()
    if isinstance(obj, list):
        for item in obj:
            found |= _collect_cited_ids(item)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k == "refs" and isinstance(v, list):
                found |= {str(x) for x in v}
            elif k in ("job_id", "comment_id") and isinstance(v, str) and v:
                found.add(v)
            else:
                found |= _collect_cited_ids(v)
    return found


def _sanitize(obj, *, comment_ids, video_ids):
    """递归清理 report 结构，返回一份新对象（不修改入参）。

    规则（comment 与 video 两个命名空间分别核对）：
    - 任意名为 refs 的列表：只保留 str(x) 在 comment_ids 中的元素。
    - 列表中的字典元素，若带有非空 job_id 且不在 video_ids 中：整项丢弃（覆盖 sources）。
    - 字典中名为 comment_id 的字段，若值不在 comment_ids 中：整个字段剔除。
    - 其余字典 / 列表递归处理，标量原样返回。
    """
    if isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict) and item.get("job_id") \
                    and str(item["job_id"]) not in video_ids:
                continue
            out.append(_sanitize(item, comment_ids=comment_ids, video_ids=video_ids))
        return out

    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if k == "refs" and isinstance(v, list):
                new[k] = [cid for cid in v if str(cid) in comment_ids]
            elif k == "comment_id":
                if v in comment_ids:
                    new[k] = v
                # 否则丢弃该字段（不写入 new）
            else:
                new[k] = _sanitize(v, comment_ids=comment_ids, video_ids=video_ids)
        return new

    return obj


def validate_report(report: dict, evidence_store) -> tuple[dict, dict]:
    """校验并清理报告中的证据引用。

    Args:
        report: LLM 产出的策略报告 JSON（不会被原地修改）。
        evidence_store: 本次运行的 EvidenceStore，作为引用是否合法的唯一依据。

    Returns:
        (cleaned_report, validation)。validation 含：
        - total_refs: 报告中引用到的去重 ID 数量
        - valid_refs: 其中可在证据库回查到的去重 ID 数量
        - rejected_refs: 无法回查、已被剔除的 ID（升序去重列表）
        - rejected_count: len(rejected_refs)
        - notes: 剔除说明（无剔除时为空列表）
    """
    comment_ids = {r.comment_id for r in evidence_store.all_records() if r.comment_id}
    video_ids = {r.job_id for r in evidence_store.all_records() if r.job_id}

    # comment 与 video 两个命名空间互不重叠（数据层面不会出现同值），
    # 因此用并集判断某个被引用 ID 是否存在于证据库中即可。
    valid_ids = comment_ids | video_ids

    cited = _collect_cited_ids(report)
    valid = cited & valid_ids
    invalid = cited - valid_ids

    cleaned = _sanitize(report, comment_ids=comment_ids, video_ids=video_ids)

    notes = []
    if invalid:
        notes.append(f"剔除 {len(invalid)} 条无法在证据库回查的引用")

    validation = {
        "total_refs": len(cited),
        "valid_refs": len(valid),
        "rejected_refs": sorted(invalid),
        "rejected_count": len(invalid),
        "notes": notes,
    }
    return cleaned, validation
