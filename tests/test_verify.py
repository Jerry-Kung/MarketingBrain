"""报告引用校验测试。"""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.verify import validate_report
from app.store.evidence import EvidenceStore
from app.datasource.models import CommentRecord


def _store():
    st = EvidenceStore("t1")
    st.register_comment(CommentRecord(comment_id="c1", content="ok", job_id="j1"), source="s")
    st.register_video(job_id="j1", video_title="坦克300", comment_count=5, source="s")
    return st


def test_all_valid_refs_kept():
    store = _store()
    report = {"themes": [{"theme": "油耗", "refs": ["c1"]}],
              "sources": [{"job_id": "j1", "video_title": "坦克300"}]}
    cleaned, validation = validate_report(report, store)
    assert validation["valid_refs"] == validation["total_refs"]
    assert validation["rejected_refs"] == []
    assert len(cleaned["themes"][0]["refs"]) == 1


def test_invalid_comment_ref_removed():
    store = _store()
    report = {"themes": [{"theme": "油耗", "refs": ["c1", "c_fake"]}]}
    cleaned, validation = validate_report(report, store)
    assert "c_fake" in validation["rejected_refs"]
    assert "c_fake" not in cleaned["themes"][0]["refs"]
    assert "c1" in cleaned["themes"][0]["refs"]


def test_invalid_video_ref_removed():
    store = _store()
    report = {"sources": [{"job_id": "j1", "video_title": "a"}, {"job_id": "j_fake", "video_title": "b"}]}
    cleaned, validation = validate_report(report, store)
    assert len(cleaned["sources"]) == 1
    assert cleaned["sources"][0]["job_id"] == "j1"
    assert "j_fake" in validation["rejected_refs"]


def test_no_refs_report_passes_with_zero():
    store = _store()
    report = {"themes": [{"theme": "x"}]}
    cleaned, validation = validate_report(report, store)
    assert validation["total_refs"] == 0
    assert validation["valid_refs"] == 0


def test_nested_risk_opportunity_ref_removed():
    """risk_opportunity[].refs 中的非法引用同样应被剔除。"""
    store = _store()
    report = {"risk_opportunity": [{"type": "risk", "title": "t", "reason": "r",
                                     "refs": ["c1", "c_fake"]}]}
    cleaned, validation = validate_report(report, store)
    assert "c_fake" in validation["rejected_refs"]
    assert cleaned["risk_opportunity"][0]["refs"] == ["c1"]


def test_stray_comment_id_field_dropped():
    """actions 中混入的孤立 comment_id 字段应被剔除且不报错。"""
    store = _store()
    report = {"actions": [{"title": "a", "comment_id": "c_fake"}]}
    cleaned, validation = validate_report(report, store)
    assert "c_fake" in validation["rejected_refs"]
    assert "comment_id" not in cleaned["actions"][0]
    assert cleaned["actions"][0]["title"] == "a"


def test_input_report_not_mutated():
    """校验过程不应修改传入的原始 report 对象。"""
    store = _store()
    report = {"themes": [{"theme": "油耗", "refs": ["c1", "c_fake"]}],
              "sources": [{"job_id": "j1", "video_title": "a"}, {"job_id": "j_fake", "video_title": "b"}]}
    original = copy.deepcopy(report)
    validate_report(report, store)
    assert report == original


def test_distinct_ref_counted_once():
    """同一 ID 被多次引用时，total_refs / valid_refs 只计一次。"""
    store = _store()
    report = {"themes": [{"theme": "a", "refs": ["c1"]}, {"theme": "b", "refs": ["c1"]}]}
    cleaned, validation = validate_report(report, store)
    assert validation["total_refs"] == 1
    assert validation["valid_refs"] == 1


def test_video_job_id_cited_in_refs_is_rejected():
    """refs 只认 comment 命名空间：把 video 的 job_id 塞进 refs 应被剔除并计入 rejected。"""
    store = _store()
    report = {"themes": [{"theme": "x", "refs": ["j1"]}]}
    cleaned, validation = validate_report(report, store)
    assert cleaned["themes"][0]["refs"] == []
    assert "j1" in validation["rejected_refs"]
    assert validation["valid_refs"] == 0


def test_comment_id_cited_as_source_job_id_is_rejected():
    """sources[].job_id 只认 video 命名空间：把 comment_id 塞进 job_id 应整项丢弃并计入 rejected。"""
    store = _store()
    report = {"sources": [{"job_id": "c1", "video_title": "x"}]}
    cleaned, validation = validate_report(report, store)
    assert cleaned["sources"] == []
    assert "c1" in validation["rejected_refs"]


def test_int_job_id_normalized_and_checked():
    """job_id 为 int 类型时，未注册应被剔除并按字符串计入 rejected；注册后应保留并计入 valid。"""
    store = _store()
    report = {"sources": [{"job_id": 123, "video_title": "x"}]}

    cleaned, validation = validate_report(report, store)
    assert cleaned["sources"] == []
    assert "123" in validation["rejected_refs"]

    store2 = _store()
    store2.register_video(job_id="123", video_title="c", comment_count=1, source="s")
    cleaned2, validation2 = validate_report(report, store2)
    assert cleaned2["sources"][0]["job_id"] == 123
    assert validation2["valid_refs"] == 1
    assert "123" not in validation2["rejected_refs"]


def test_int_comment_id_normalized_and_valid():
    """comment_id 字段为 int 类型，但注册的证据 ID 为对应字符串时应被识别为合法。"""
    store = _store()
    store.register_comment(CommentRecord(comment_id="42", content="ok", job_id="j1"), source="s")
    report = {"comment_id": 42}
    cleaned, validation = validate_report(report, store)
    assert cleaned == {"comment_id": 42}
    assert validation["valid_refs"] == 1
    assert validation["rejected_refs"] == []
