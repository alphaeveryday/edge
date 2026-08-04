"""분석 기록 — statics 산출을 **기존 계보 체인**에 싣는다. 새 표를 만들지 않는다.

## 정본 체인 (ERD 실측)

    price_movement_trigger ─┐
    minute_price_trigger ───┴→ etf_contribution_observation (+member)
                               → explanation_route
                               → explanation_run
                               → explanation_result
                               + analysis_evidence_bundle

계보 id 는 **소비한 트리거에서 파생**한다(`stable_id`). 그래서 DB 에 있는 그 트리거
행에 계보가 매달리고, 재실행이 같은 id 를 낸다(전부 ON CONFLICT DO NOTHING).

## 두 설명이 한 행에 들어간다

새 `explanation_type` 어휘를 만들지 않았다 - CHECK 가 네 값에 묶여 있고, 그 어휘는
**근거 성격**을 뜻한다. 우리 판정이 정확히 그것을 낸다:

    EVENT_SUPPORTED  성립 엣지·통과 함의가 있다
    PRICE_ONLY       시장 환원만 - 사건은 무유의
    MIXED            혼합 라우팅 (어느 층도 지배 못 함)
    UNCERTAIN        전부 판정불가

    summary   정직한 설명 전문 (통계량·게이트·구간)
    headline  쉬운 설명 (토스식, 수치 없음) - 원장 주석이 'MTS 카드/목록용 한 줄'
    stage_results  층 회계 · 라우팅 · 게이트 판정 · 근거 묶음 id · 창 몫

## 왜 psycopg 별 연결인가

레이크의 `rdb` 는 `READ_ONLY` 로 붙어 있다. 쓰기는 별 연결이어야 한다.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

PIPELINE_ID = "alphamale-etf-daily-v1"      # observability.stable_id 와 같은 재료
DSN_ENV = "EDGE_RDB_DSN"
BUNDLE_VERSION_ENV = "ALPHAMALE_RELEASE_BUNDLE_VERSION"

TYPES = ("PRICE_ONLY", "EVENT_SUPPORTED", "MIXED", "UNCERTAIN")
LEVELS = ("HIGH", "MEDIUM", "LOW")

# 우리 라우팅 → 원장 `route_code` 어휘. **어휘를 늘리지 않는다** - CHECK 가 5종이고
# 그 뜻이 우리 5분기와 일대일로 맞는다. 늘리면 마이그레이션이 필요하고, 서빙이
# 모르는 코드를 받는다.
#   시장·섹터  공통요인이 끌었다        -> COMMON_FACTOR
#   고유        상위 종목에 집중됐다     -> CONCENTRATED
#   혼합        어느 층도 지배 못 함     -> FALLBACK_2LEG (2레그 폴백이 정확히 그 뜻)
#   괴리단독    ETF 수급·유동성          -> FLOW_DOMINATED
#   미상        가격 회계만 남았다       -> PRICE_ONLY
ROUTE_CODES = {"시장": "COMMON_FACTOR", "섹터": "COMMON_FACTOR",
               "고유": "CONCENTRATED", "혼합": "FALLBACK_2LEG",
               "괴리단독": "FLOW_DOMINATED"}
# CHECK 가 강제한다: COMMON_FACTOR·CONCENTRATED 는 event_search 필수, 나머지는 금지.
# 그래서 `event_search` 를 호출자가 정하게 두면 안 된다 - 코드에서 파생한다.
EVENT_SEARCH = {"COMMON_FACTOR", "CONCENTRATED"}


def route_code_of(route_kind: str) -> tuple[str, bool]:
    """라우팅 → (원장 코드, event_search). 미상은 PRICE_ONLY 로 떨어진다."""
    code = ROUTE_CODES.get(route_kind, "PRICE_ONLY")
    return code, code in EVENT_SEARCH


def stable_id(prefix: str, *parts: str) -> str:
    """결정적 발번 (ADR-0027 의 결정적 계열). 구분자는 `\\x01` - 원장과 같아야 한다."""
    material = "\x01".join((PIPELINE_ID, *(str(p) for p in parts)))
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:26]}"


@dataclass(frozen=True, slots=True)
class Verdicts:
    """이 셀의 통계 판정 요약. **설명 성격과 확신도를 이것이 정한다** - 모델이 아니다."""

    applied_edges: int = 0          # 오늘 적용된 성립 엣지
    credible: int = 0               # 진단 셋을 통과한 함의
    significant_market: int = 0     # 유의한 시장 사건 시행
    undecided: int = 0              # 판정불가
    route_kind: str = ""            # 시장 | 섹터 | 고유 | 혼합 | 괴리단독
    idio_qualified: bool = True     # 고유 자격 (잔차 공통상관)
    bundles: tuple[str, ...] = ()   # 근거 묶음 id

    @property
    def explanation_type(self) -> str:
        """근거 성격. **어휘를 늘리지 않는다** - CHECK 가 네 값이고 그것으로 충분하다."""
        if self.applied_edges or self.credible or self.significant_market:
            return "EVENT_SUPPORTED"
        if self.route_kind == "혼합":
            return "MIXED"
        if self.route_kind in ("시장", "섹터", "고유", "괴리단독"):
            # 사건이 아무것도 안 섰다 - 가격 회계만 남았다. 그것도 답이다.
            return "PRICE_ONLY"
        return "UNCERTAIN"

    @property
    def confidence_level(self) -> str:
        """확신도. 고유 자격 미달·판정불가 다수는 LOW 로 내린다 - 숨기지 않는다."""
        if not self.idio_qualified:
            return "LOW"
        if self.applied_edges or self.credible:
            return "HIGH" if self.undecided == 0 else "MEDIUM"
        return "LOW" if self.undecided else "MEDIUM"


def _selfcheck() -> None:
    assert stable_id("cob", "pmt_X").startswith("cob_")
    assert len(stable_id("cob", "pmt_X")) == 30
    assert stable_id("cob", "pmt_X") == stable_id("cob", "pmt_X")   # 결정적
    assert stable_id("cob", "pmt_X") != stable_id("cob", "pmt_Y")

    # 판정 → 어휘. **어휘를 늘리지 않는다**
    assert Verdicts(applied_edges=1, route_kind="고유").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(credible=2, route_kind="시장").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(significant_market=1, route_kind="시장").explanation_type == "EVENT_SUPPORTED"
    assert Verdicts(route_kind="시장").explanation_type == "PRICE_ONLY"
    assert Verdicts(route_kind="혼합").explanation_type == "MIXED"
    assert Verdicts().explanation_type == "UNCERTAIN"
    assert all(Verdicts(route_kind=k).explanation_type in TYPES
               for k in ("시장", "섹터", "고유", "혼합", "괴리단독", ""))

    # 고유 자격 미달은 무조건 LOW - 숨기지 않는다
    assert Verdicts(applied_edges=3, idio_qualified=False).confidence_level == "LOW"
    assert Verdicts(applied_edges=1).confidence_level == "HIGH"
    assert Verdicts(applied_edges=1, undecided=2).confidence_level == "MEDIUM"
    assert Verdicts(undecided=4).confidence_level == "LOW"
    assert all(Verdicts(applied_edges=n, undecided=m).confidence_level in LEVELS
               for n in (0, 1) for m in (0, 3))

    # 라우팅 매핑은 CHECK 두 개를 동시에 만족해야 한다
    for k, want in (("시장", "COMMON_FACTOR"), ("고유", "CONCENTRATED"),
                    ("혼합", "FALLBACK_2LEG"), ("괴리단독", "FLOW_DOMINATED"),
                    ("", "PRICE_ONLY")):
        c, sch = route_code_of(k)
        assert c == want, (k, c)
        assert sch == (c in EVENT_SEARCH)
    print("ok")


if __name__ == "__main__":
    _selfcheck()

# ── 배포 경로 접합 (ALPHA-671) ────────────────────────────────────────────────
# 기존 writer(`eventstore.persist_explanation`)는 **테넌트 팬아웃과 게시 게이트**를
# 갖고 있다(advisory lock · 그날 첫 결과만 PUBLISHED · tenant_delivery 채번). 그것을
# 우회하면 산출은 남는데 **아무에게도 배달되지 않는다**. 그래서 계보 적재를 새로
# 만들지 않고, statics 산출을 그 writer 가 먹는 모양(`Explanation`)으로 바꾼다.
#
# `Explanation` 은 dict 뷰다: verdict/confidence 를 한글 어휘로 주면 기존 매핑이
# 원장 enum 으로 옮긴다. **어휘를 늘리지 않는다** - 그 매핑이 이미 정본이다.
VERDICT_WORDS = {"EVENT_SUPPORTED": "공식 이벤트 선행", "MIXED": "시장·섹터 주도",
                 "PRICE_ONLY": "수급·흐름 추정", "UNCERTAIN": "원인 미확인"}
CONFIDENCE_WORDS = {"HIGH": "높음", "MEDIUM": "중간", "LOW": "보류"}


def as_explanation(honest: str, headline: str, v: Verdicts, stage: dict):
    """statics 산출 → `domain.models.Explanation`. 기존 영속 경로가 그대로 받는다."""
    from ..domain.models import Explanation
    return Explanation(raw={
        # `explain` 이 summary 가 된다(정직한 설명 전문).
        "explain": honest,
        # `headline` 은 MTS 카드용 한 줄 - 쉬운 설명(토스식)이 그 자리다.
        "headline": headline,
        "verdict": VERDICT_WORDS.get(v.explanation_type, "원인 미확인"),
        "confidence": CONFIDENCE_WORDS.get(v.confidence_level, "보류"),
        # 원장이 버리는 것을 런 아카이브가 남긴다 - 층 회계·판정·근거 묶음 id.
        "stage_results": stage,
        "evidence_bundles": list(v.bundles),
    })


def verdicts_from(text: str, *, route_kind: str, idio_qualified: bool,
                  bundles: tuple[str, ...] = ()) -> Verdicts:
    """산출 산문 → 판정 요약. **게이트가 낸 어휘만 센다** - '유의' 를 그냥 세면
    '무유의' 줄에도 걸린다."""
    return Verdicts(
        applied_edges=text.count("→ **오늘 적용**"),
        credible=text.count("[함의]") - text.count("[함의] 없음"),
        significant_market=text.count("**유의**"),
        undecided=text.count("판정불가"),
        route_kind=route_kind, idio_qualified=idio_qualified, bundles=bundles)
