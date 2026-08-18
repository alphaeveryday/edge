# 데이터 모델

Cloud 도메인 간 흐름은 overview로, 상세 물리 FK는 각 도메인의 ERD로 구분한다.

## Cloud 도메인 개요

![Cloud 데이터 모델 개요](cloud-overview.svg)

## Cloud 도메인별 물리 관계

- [기준정보와 금융상품](domains/reference/)
- [문서와 공시 사실](domains/documents/)
- [시장 데이터와 수집 상태](domains/market/)
- [이벤트와 근거](domains/events/)
- [설명 생성과 계보](domains/explanation/)
- [테넌트 전송](domains/delivery/)
- [운영 원장](domains/operations/)

도메인 경계를 넘는 FK의 대상 테이블은 관계 이해에 필요한 컨텍스트로 상세 ERD에 포함한다.

## On-Prem 세트

Flyway 세트는 둘이고 위 도메인은 Cloud 세트다. 증권사 관리 환경에 배포되는 On-Prem 세트는
13테이블이라 도메인으로 쪼개지 않고 한 장으로 둔다 — [온프렘 테넌트 콘솔](onprem/).

온프렘 SVG 는 draw.io CLI export 이고, 컬럼 행 라벨을 HTML 표가 아니라 평문으로 둔다.
HTML 라벨은 export 에 래스터 폴백 `<image>` 를 함께 낳는데(41KB → 712KB, 그중 621KB 가
base64 PNG), markdown 에 박힌 SVG 는 `foreignObject` 를 렌더하지 않아 그 래스터가 실제로
보이는 층이 된다 — 확대하면 흐려지고 문자열 검색도 안 된다.

**배경은 흰색으로 통일한다.** draw.io CLI 는 투명 배경 + `color-scheme: light dark` 를 내므로
그대로 커밋하면 GitHub 다크 테마에서 배경이 검게 깔린다 — 그림이 보는 사람 테마에 따라
달라진다. drawio 파일의 `background` 속성은 SVG export 에 실리지 않으니(실측) export 뒤에
전면 흰 배경을 넣는다:

```bash
drawio --export --format svg --svg-theme light --output <경로>/erd.svg <경로>/erd.drawio
python3 - "<경로>/erd.svg" <<'PY'
import re, sys
from pathlib import Path
p = Path(sys.argv[1]); t = p.read_text()
t = t.replace("background: transparent; background-color: transparent;", "background-color: #ffffff;", 1)
m = re.search(r"<svg\b[^>]*>", t)
p.write_text(t[: m.end()] + '<rect width="100%" height="100%" fill="#ffffff"/>' + t[m.end() :])
PY
```

손으로 쓴 SVG 는 같은 일을 `.canvas` 클래스(`fill:#ffffff`)로 한다. `validate-doc-erd.py` 가
둘(다크 분기 없음·뷰박스를 덮는 흰 rect)을 검사하므로 빼먹으면 `data-model-validate` 가
빨간불을 낸다.
