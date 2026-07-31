"""도메인 문서 조회 — **에이전트가 산업 지식을 필요할 때 꺼내 본다.**

왜 브리프에 싣지 않고 조회로 두나. 도메인 문서는 청크 수천 개다. 브리프에 넣으면 토큰이
폭발하고, 무엇을 실을지 우리가 고르는 순간 그것이 다시 "어디를 보라는 지시"가 된다 -
프롬프트에서 걷어낸 것이 데이터 경로로 되살아난다. 그래서 **에이전트가 질의를 만들어
필요할 때 부른다.**

저장 배치는 `experiments/rag/domain_rag.py` 가 만든 것을 그대로 읽는다:

    s3://<bucket>/index/<sector>/<industry>/chunks.parquet   청크 + 임베딩(1024)
    s3://<bucket>/docs/<sector>/<industry>/<ticker>-<ord>.txt 원문

두 단계다. 임베딩 코사인으로 넓게 긷고(어휘가 달라도 주제가 같은 것을 데려온다),
rerank 로 좁힌다(질의와 문서를 함께 보고 순위를 다시 매긴다). 에이전트가 읽는 것은
상위 몇 개뿐이라 그 자리의 정확도가 값을 낸다.

리전이 갈린다. **서울에 rerank 모델이 없다**(실측: ap-northeast-2 는 embed 만).
임베딩은 서울에서, 재순위는 us-west-2 에서 돈다. 이 저장소가 공시 원문만 받는 이유가
여기 있다 - 애널리스트 리포트(증권사 저작물)나 비공개 자료를 넣으면 리전 이동이
계약·저작권 문제가 된다. 넣지 않는다.

의존은 지연 로드다. boto3 가 없는 환경(단위 테스트)에서 import 만으로 죽지 않아야 한다.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

REGION_EMBED = "ap-northeast-2"
REGION_RERANK = "us-west-2"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
RERANK_MODEL = "amazon.rerank-v1:0"
EMBED_DIM = 1024


@dataclass
class DomainDocs:
    """도메인 문서 검색기. **인덱스는 한 번 읽고 프로세스 안에 둔다.**

    벡터 DB 를 쓰지 않는 이유: 청크 수천~수만 개에서 numpy 행렬곱은 밀리초다. 관리형
    벡터 검색은 최소 과금이 월 수백 달러이므로, 규모가 그 비용을 정당화하기 전에는
    parquet 을 읽어 직접 곱하는 쪽이 맞다. 십만 청크를 넘으면 그때 바꾼다.
    """

    bucket: str
    profile: str | None = None
    s3: Any = None
    embedder: Any = None                  # (list[str]) -> np.ndarray. 테스트에서 주입
    reranker: Any = None                  # (str, list[str], int) -> list[(idx, score)]
    _meta: list[dict] = field(default_factory=list)
    _mat: np.ndarray | None = None
    _loaded: set[str] = field(default_factory=set)
    _all: bool = False                    # 전 도메인을 다 올렸나

    # ── AWS 지연 로드 ────────────────────────────────────────────────
    def _session(self, region: str):
        import boto3
        return boto3.Session(profile_name=self.profile, region_name=region)

    def _client(self):
        if self.s3 is None:
            self.s3 = self._session(REGION_EMBED).client("s3")
        return self.s3

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self.embedder is not None:
            return np.asarray(self.embedder(texts), dtype=np.float32)
        br = self._session(REGION_EMBED).client("bedrock-runtime")
        out = np.zeros((len(texts), EMBED_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            r = br.invoke_model(modelId=EMBED_MODEL, body=json.dumps(
                {"inputText": t[:8000], "dimensions": EMBED_DIM, "normalize": True}))
            out[i] = np.asarray(json.loads(r["body"].read())["embedding"],
                                dtype=np.float32)
        return out

    def _rerank(self, query: str, docs: list[str], k: int) -> list[tuple[int, float]]:
        if self.reranker is not None:
            return list(self.reranker(query, docs, k))
        ba = self._session(REGION_RERANK).client("bedrock-agent-runtime")
        r = ba.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {
                "type": "TEXT", "textDocument": {"text": d}}} for d in docs],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "numberOfResults": min(k, len(docs)),
                    "modelConfiguration": {"modelArn": (
                        f"arn:aws:bedrock:{REGION_RERANK}::"
                        f"foundation-model/{RERANK_MODEL}")}}})
        return [(x["index"], float(x["relevanceScore"])) for x in r["results"]]

    # ── 인덱스 ──────────────────────────────────────────────────────
    def domains(self) -> list[str]:
        """있는 도메인 목록. **에이전트가 무엇을 물을 수 있는지 알아야 한다.**"""
        s3 = self._client()
        out: list[str] = []
        tok = None
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": "index/"}
            if tok:
                kw["ContinuationToken"] = tok
            r = s3.list_objects_v2(**kw)
            out += [o["Key"][len("index/"):-len("/chunks.parquet")]
                    for o in r.get("Contents", [])
                    if o["Key"].endswith("chunks.parquet")]
            if not r.get("IsTruncated"):
                break
            tok = r.get("NextContinuationToken")
        return sorted(out)

    def load(self, domain: str | None = None) -> int:
        """도메인 인덱스를 올린다. 이미 올린 것은 다시 읽지 않는다."""
        import pyarrow.parquet as pq

        s3 = self._client()
        keys = [f"index/{d}/chunks.parquet"
                for d in ([domain] if domain else self.domains())]
        added = 0
        mats = [self._mat] if self._mat is not None else []
        for key in keys:
            if key in self._loaded:
                continue
            body = s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            tb = pq.read_table(io.BytesIO(body))
            cols = tb.column_names
            need = {"text", "vec", "ticker", "ord"}
            if not need <= set(cols):
                raise ValueError(f"{key}: 컬럼이 모자라다 {sorted(need - set(cols))}")
            d = key[len("index/"):-len("/chunks.parquet")]
            texts = tb.column("text").to_pylist()
            tks = tb.column("ticker").to_pylist()
            ords = tb.column("ord").to_pylist()
            vecs = np.asarray(tb.column("vec").to_pylist(), dtype=np.float32)
            self._meta += [{"domain": d, "ticker": t, "ord": o, "text": x}
                           for t, o, x in zip(tks, ords, texts)]
            mats.append(vecs)
            self._loaded.add(key)
            added += len(texts)
        if mats:
            self._mat = np.vstack(mats)
        if domain is None:
            self._all = True
        return added

    # ── 검색 ────────────────────────────────────────────────────────
    def search(self, query: str, *, domain: str | None = None, k: int = 6,
               pool: int = 50, rerank: bool = True) -> list[dict]:
        """상위 k 개 청크. **원문 출처(도메인·티커·순서)를 항상 함께 낸다.**

        출처가 없으면 에이전트가 읽은 것을 사후에 확인할 수 없다 - 산문 근거도 수치와
        같은 규율을 받아야 한다.
        """
        # **도메인 지정 검색이 이후의 전 도메인 검색을 오염시키지 않게 한다.**
        # 실연결 확인에서 잡혔다: 반도체만 올린 뒤 도메인 없이 "은행 이자이익"을 물었더니
        # 반도체 42청크에서만 찾고 조용히 그 안의 1위를 냈다. 결과가 줄어든 것이 어디에도
        # 드러나지 않는 것이 이 종류 버그의 성질이다.
        if domain:
            if f"index/{domain}/chunks.parquet" not in self._loaded:
                self.load(domain)
        elif not self._all:
            self.load(None)
        if self._mat is None or not self._meta:
            return []
        idx = ([i for i, m in enumerate(self._meta) if m["domain"] == domain]
               if domain else list(range(len(self._meta))))
        if not idx:
            return []
        sim = self._mat[idx] @ self._embed([query])[0]
        top = np.argsort(-sim)[:min(pool, len(sim))]
        cand = [{**self._meta[idx[j]], "cosine": float(sim[j])} for j in top]
        if not rerank or len(cand) <= k:
            return cand[:k]
        try:
            order = self._rerank(query, [c["text"] for c in cand], k)
        except Exception:  # noqa: BLE001 - rerank 실패는 검색 실패가 아니다
            return cand[:k]
        return [{**cand[i], "rerank": s} for i, s in order]
