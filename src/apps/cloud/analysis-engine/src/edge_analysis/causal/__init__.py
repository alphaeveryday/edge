"""인과귀속 P0–P9 — **그림에 대한 진술이 아니라 세계에 대한 진술을 만든다.**

설계도: `docs/analysis-engine/architecture/causal-attribution-p0p9.drawio`

    contracts       단계 사이를 지나는 값 전부. 계약이 바뀌면 여기부터 바뀐다
    p0_question     설명 대상과 **반사실**을 문장으로 고정. 답의 형태를 먼저 선언한다
    p1_fingerprint  관측 자신의 지문. 가설 이전·LLM 이전에 후보를 죽일 재료를 만든다
    p2_hypotheses   다중 작업가설. **어휘 무제한**, 세션 n개 독립
    p3_graph        공통원인 완비 의무(Hernán 조건) + 배정기제 U 자동 삽입
    p4_identify     식별 3값. `not_identified` 는 실패가 아니라 정상 종료다
    p5_discriminate ★ 가설쌍과 **선언된 U 마다** 소거 검정. 못 적으면 미소거로 확정
    p6_sensitivity  E-value. 식별이 안 될 때 주장의 강도를 재는 유일한 축
    p7_negative     음성 대조 · 혼재 공시 스크린
    p8_findings     처분 원장. 기여/비기여/미결 전건 명시 + 주장 상한 강제
    p9_registry     메커니즘 레지스트리. 단일 사례는 반복으로만 검정력을 얻는다

검정 실행 기계는 그대로 남아 P5 뒤에 붙는다:

    graph    ADMG(양방향 포함)·d/m-분리·함의 조건부독립·뒷문·도구변수 열거
    verify   간선 하나를 샌드박스에서 **코드로** 추정 + G1~G7b 게이트
    sandbox  제한 exec 네임스페이스·원장(placebo 호출 전량)·PIT 강제 도구 바인딩
    engine   축약 경로(술어 선언 → 고정 추정량). 샌드박스 OFF 일 때만 쓴다
    chain    간선 유형별 증명 양식 · 예산 정합
    stats    placebo(순열 귀무)·permute(층화)·residualize/fit/predict
    fit      국소 적합(부분상관 CI) + 전역 적합(Shipley C). 발견 루프 전용
    run      P0–P9 오케스트레이션

데이터 접근은 여기 없다. `..adapters.causal_data` 가 코호트·정렬열을,
`..adapters.sql_surface` 가 P2·P3·P5 의 자유 질의를 공급한다.
"""
from . import (
    chain,
    contracts,
    engine,
    fit,
    graph,
    p0_question,
    p1_fingerprint,
    p2_hypotheses,
    p3_graph,
    p4_identify,
    p5_discriminate,
    p6_sensitivity,
    p7_negative,
    p8_findings,
    p9_registry,
    sandbox,
    stats,
    verify,
)

__all__ = [
    "chain", "contracts", "engine", "fit", "graph",
    "p0_question", "p1_fingerprint", "p2_hypotheses", "p3_graph", "p4_identify",
    "p5_discriminate", "p6_sensitivity", "p7_negative", "p8_findings", "p9_registry",
    "sandbox", "stats", "verify",
]
