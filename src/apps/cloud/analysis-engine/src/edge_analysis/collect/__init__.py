"""수집기 — 벤더 원천을 레이크 canonical 로 올리는 절차를 코드로 남기는 자리.

왜 analysis-engine 안에 있나: 정식 수집은 data-pipeline 레인 소관이지만 canonical
5분봉 잡이 거기에 **없다**. 그 사이 표를 쌓은 것은 `.tmp/` 의 미추적 스크립트와 사람의
손이었고, 그러면 기계 한 대와 함께 갱신 방법이 사라진다. 여기 있는 것은 임시 배관이
아니라 **갱신 절차의 유일한 사본**이다.

하위 모듈을 여기서 다시 export 하지 않는다 — `python -m edge_analysis.collect.intraday`
로 부를 때 패키지가 모듈을 먼저 import 해 runpy 경고가 난다.
"""
from __future__ import annotations
