# ADR-0035: 위젯 UI를 빌드 산출물로 납품 — 실행 서버 없음

- 상태: 승인됨 — 런타임 경로 문면(위젯→증권사 백엔드/API GW→Publication API)과 "Publication API 계약 영향 없음" 결과 조항은 대체됨 → [ADR-0053](0053-widget-direct-serving-no-personalization.md) (위젯 직접 호출, 2026-08-17; 빌드 산출물 납품 결정 자체는 불변)
- 날짜: 2026-07-21

## 맥락
하이브리드 피벗([ADR-0010](0010-hybrid-onprem-pivot.md))에서 벤더 클라우드가 서빙하던 embed widget과 그 백엔드 `widget-api`를 제거했다. 이후 context.md는 고객 화면을 "증권사 MTS/HTS가 직접 구성"으로만 서술해, EDGE가 UI 측 산출물을 전혀 제공하지 않는 것처럼 읽혔다. 실제로는 가격 변동 설명의 노출 표현(레이아웃·상태 배지·근거 표시)을 증권사마다 새로 만들게 하면 통합 부담이 크고 제품의 설명 UX가 파편화된다. "EDGE가 위젯 UI를 주긴 하는데 어떤 형태로 주느냐"가 미정이었다.

## 결정
**EDGE 위젯 UI를 빌드 산출물(정적 자산 번들)로 납품한다 — 벤더 실행 서버 없음.**

- 위젯 UI는 빌드된 정적 자산(JS/CSS/에셋)으로 증권사에 전달된다. 벤더가 호스팅하는 런타임·서버·엔드포인트가 없다 — `widget-api` 서버 제거([ADR-0010](0010-hybrid-onprem-pivot.md)) 결정은 유지된다.
- 증권사가 이 산출물을 자기 MTS/HTS에 임베드·호스팅한다(웹뷰·iframe·직접 임베드 등 증권사 선택). 자산의 거주지·전송 경로 모두 증권사 환경 안이다.
- 런타임 데이터 경로는 불변이다: 고객 브라우저 → (임베드된 위젯 UI) → **증권사 백엔드/API GW** → **On-Prem Publication API**. 위젯 UI가 벤더 클라우드를 직접 호출하지 않고, Publication API는 Published 상태만 반환한다([publication-api.md](../contracts/publication-api.md)).
- 증권사가 자체 UI를 구축하는 선택지도 유효하다 — EDGE 위젯 UI 납품은 통합 부담을 줄이는 **기본 제공물**이지 강제가 아니다.

즉 "위젯 없음"이 아니라 "위젯 UI는 있으나 **산출물 납품 형태**이고 실행 주체는 증권사"다. ADR-0010의 "MVP에 embed widget 없음"은 **벤더가 서빙하는 embed widget이 없다**는 뜻으로 좁혀 읽는다.

## 대안
- **UI 무제공(증권사 전량 자체 구축)** — 통합 부담·설명 UX 파편화. 표준 노출 표현을 벤더가 통제하지 못해 배제.
- **벤더 호스팅 위젯 런타임 부활(widget-api 재도입)** — 고객 접점·자산을 벤더 환경에 두게 돼 데이터 주권·준법감시인 통제 요구([ADR-0010](0010-hybrid-onprem-pivot.md))와 정면 충돌. 배제.

## 결과
- context.md §2 고객 화면 행·§5 변경표(widget-api 행)를 "위젯 UI 빌드 산출물 납품(서버 없음)"으로 갱신한다.
- 납품물은 빌드 아티팩트 경계에 속한다(온프렘 배포 산출물과 함께 전달). 벤더 런타임·도메인·인증서가 위젯 UI에 붙지 않는다.
- 데이터 거주지([data-residency.md](../domain/data-residency.md))·Publication API 계약은 영향 없음 — 위젯 UI는 표현 계층일 뿐 새 데이터 표면을 만들지 않는다.
