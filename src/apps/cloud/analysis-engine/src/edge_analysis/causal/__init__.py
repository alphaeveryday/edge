"""인과 설계 하네스 — 순수 로직.

  graph   ADMG(양방향 포함)·d/m-분리·함의 조건부독립·뒷문·도구변수 열거
  fit     국소 적합(부분상관 CI) + 전역 적합(Shipley C) — MI 는 국소 p 순위가 대체
  stats   placebo(순열 귀무)·permute(층화)·residualize/fit/predict

데이터 접근은 여기 없다. `..adapters.causal_data` 가 코호트·정렬열을 공급한다.
"""
from . import fit, graph, stats

__all__ = ["fit", "graph", "stats"]
