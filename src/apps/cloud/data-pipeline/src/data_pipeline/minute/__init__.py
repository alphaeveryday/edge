"""1분 가격·뉴스 파이프라인 (ALPHA-660~).

정본: `.dev/edge-minute-pipeline-plan.md` + 아키텍처 결정 v0.7. 이 패키지는 PR 순서대로
자란다 — PR 1 은 공통 계약(models)·virtual clock·결정적 fake collector·JSONL 계측만 담고,
migration/repository(PR 2)·commit(PR 3)·실제 vendor adapter(PR 4+)는 후속이다.

경계: production/dev AWS·DB 를 건드리지 않는다. 자동 테스트는 fake collector 와 녹화
fixture 로만 lifecycle 을 재현한다 — 실 vendor 호출은 토스 adapter(ALPHA-682)가 승인된
경로에서만 하고, BigKinds·DeepSeek 은 아직 없다.
"""

from __future__ import annotations
