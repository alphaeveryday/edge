"""도메인 문서 조회 계약 — **출처 없는 산문은 수치 없는 주장과 같다.**

여기서 지키는 것 셋.

    1. 도메인 접두사로 검색 공간이 실제로 좁혀진다 (분기의 목적이 살아 있나)
    2. rerank 가 코사인 순위를 갈아치운다 (두 단계의 역할이 다르다)
    3. rerank 가 죽어도 검색은 산다 (재순위 실패는 검색 실패가 아니다)
"""
from __future__ import annotations

import io

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from edge_analysis.adapters.domain_docs import EMBED_DIM, DomainDocs


def _vec(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=EMBED_DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _parquet(rows: list[dict]) -> bytes:
    tb = pa.table({"chunk_id": [r["chunk_id"] for r in rows],
                   "ticker": [r["ticker"] for r in rows],
                   "ord": [r["ord"] for r in rows],
                   "text": [r["text"] for r in rows],
                   "vec": [r["vec"] for r in rows]})
    buf = io.BytesIO()
    pq.write_table(tb, buf)
    return buf.getvalue()


class _S3:
    """도메인 둘을 담은 가짜 버킷. **AWS 없이 계약을 검사한다.**"""

    def __init__(self) -> None:
        self.objects = {
            "index/Technology/Semiconductors/chunks.parquet": _parquet([
                {"chunk_id": "a1", "ticker": "000660", "ord": 0, "vec": _vec(1),
                 "text": "웨이퍼는 주요 공급사로부터 300mm 완제품으로 공급받는다"},
                {"chunk_id": "a2", "ticker": "000660", "ord": 1, "vec": _vec(2),
                 "text": "PCB 는 한국·중화권 6개사로부터 공급받는다"}]),
            "index/Basic_Materials/Steel/chunks.parquet": _parquet([
                {"chunk_id": "b1", "ticker": "005490", "ord": 0, "vec": _vec(3),
                 "text": "철광석과 석탄을 원료로 쓰며 장기 계약으로 조달한다"}]),
        }
        self.gets: list[str] = []

    def list_objects_v2(self, **kw):
        pref = kw.get("Prefix", "")
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(pref)],
                "IsTruncated": False}

    def get_object(self, *, Bucket, Key):  # noqa: N803 - boto3 시그니처를 흉내낸다
        self.gets.append(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def _docs(**kw) -> DomainDocs:
    return DomainDocs(bucket="b", s3=_S3(),
                      embedder=lambda ts: np.asarray([_vec(1) for _ in ts]), **kw)


def test_the_domain_prefix_actually_narrows_the_search_space():
    """도메인을 회사별로 갈라 놓으면 접두사로 좁히는 의미가 사라진다 - 산업 단위여야 한다."""
    d = _docs()

    assert d.domains() == ["Basic_Materials/Steel", "Technology/Semiconductors"]

    hits = d.search("웨이퍼 조달", domain="Technology/Semiconductors", rerank=False)
    assert {h["domain"] for h in hits} == {"Technology/Semiconductors"}
    # 다른 도메인 인덱스는 읽지도 않는다 - 좁히기가 비용에도 반영돼야 한다.
    assert d.s3.gets == ["index/Technology/Semiconductors/chunks.parquet"]


def test_rerank_replaces_the_cosine_order():
    """두 단계의 역할이 다르다. 임베딩은 데려오고 rerank 가 순위를 정한다."""
    called: list[int] = []

    def rr(query, docs, k):
        called.append(len(docs))
        # 코사인 순위를 뒤집어 돌려준다 - 순위가 실제로 갈리는지 보려면 충돌해야 한다
        return [(len(docs) - 1 - i, 1.0 - i * 0.1) for i in range(min(k, len(docs)))]

    d = _docs(reranker=rr)
    plain = d.search("공급 계약", rerank=False, k=3)
    ranked = d.search("공급 계약", rerank=True, k=2)

    assert called == [3], "후보를 넓게 긷지 않고 rerank 를 걸었다"
    assert ranked[0]["text"] == plain[-1]["text"]     # 코사인 꼴찌가 1위로 올라온다
    assert [h["text"] for h in ranked] != [h["text"] for h in plain[:2]]
    assert all("rerank" in h and "cosine" in h for h in ranked)   # 두 점수를 다 남긴다


def test_a_broken_reranker_does_not_break_retrieval():
    """재순위 실패는 검색 실패가 아니다 - 리전이 갈려 있어 이쪽만 죽을 수 있다."""
    def boom(query, docs, k):
        raise RuntimeError("us-west-2 rerank 접근 불가")

    hits = _docs(reranker=boom).search("원재료", k=2)

    assert len(hits) == 2 and all("rerank" not in h for h in hits)


def test_a_scoped_search_does_not_poison_the_next_global_search():
    """실연결에서 잡힌 회귀. **결과가 조용히 줄어드는 것이 이 버그의 성질이다.**

    반도체만 올린 뒤 도메인 없이 물었더니 반도체 안에서만 찾고 그 안의 1위를 냈다.
    검색 공간이 줄어든 사실이 어디에도 드러나지 않았다 - 좁히기는 명시적일 때만 옳다.
    """
    d = _docs()
    d.search("웨이퍼", domain="Technology/Semiconductors", rerank=False)

    everywhere = d.search("철광석 조달", rerank=False, k=5)

    assert {h["domain"] for h in everywhere} == {"Technology/Semiconductors",
                                                "Basic_Materials/Steel"}
    assert len(everywhere) == 3          # 가짜 버킷의 전체 청크 수


def test_every_hit_carries_its_source():
    """출처 없는 산문은 수치 없는 주장과 같다 - 사후에 확인할 수 없다."""
    for h in _docs().search("조달", rerank=False):
        assert h["domain"] and h["ticker"] and h["ord"] is not None and h["text"]


def test_a_malformed_index_fails_loud():
    """컬럼이 모자란 인덱스를 조용히 건너뛰면 검색 결과가 조용히 줄어든다."""
    d = _docs()
    tb = pa.table({"chunk_id": ["c"], "ticker": ["1"], "ord": [0], "text": ["t"]})
    buf = io.BytesIO()
    pq.write_table(tb, buf)                      # vec 열이 없는 인덱스
    d.s3.objects["index/Broken/X/chunks.parquet"] = buf.getvalue()

    with pytest.raises(ValueError, match="컬럼이 모자라다"):
        d.load("Broken/X")
