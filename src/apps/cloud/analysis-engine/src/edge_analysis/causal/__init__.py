"""인과 설계 하네스 — 제안(산문)·검정(샌드박스 코드 실행)·판정을 코드가 가른다.

  agents   제안 에이전트: **산문 DAG**(무엇을 주장하나·왜·무엇이면 죽나)
  graph    ADMG(양방향 포함)·d/m-분리·함의 조건부독립·뒷문·도구변수 열거
  verify   검정 에이전트: 간선 하나를 샌드박스에서 **코드로** 추정 + G1~G7 게이트
  sandbox  제한 exec 네임스페이스·원장(placebo 호출 전량)·PIT 강제 도구 바인딩
  engine   축약 경로(술어 선언 → 고정 추정량). 샌드박스 OFF 일 때만 쓴다
  fit      국소 적합(부분상관 CI) + 전역 적합(Shipley C) — MI 는 국소 p 순위가 대체
  stats    placebo(순열 귀무)·permute(층화)·residualize/fit/predict
  narrate  고객 문장 + **감사 흔적**(설계·산문·원장·코드·반증 표면·데이터 요청)
  run      비용 순 오케스트레이션 (산술 → 제안 → 구조 → 형식 → 식별 → 검정 → 적합 → 서술)

데이터 접근은 여기 없다. `..adapters.causal_data` 가 코호트·정렬열을 공급한다.
"""
from . import agents, fit, graph, narrate, sandbox, stats, verify

__all__ = ["agents", "fit", "graph", "narrate", "sandbox", "stats", "verify"]
