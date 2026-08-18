# 체크포인트 — 다중 인스턴스 캐시 실험 단계 A (구현 + 스모크)

> 2026-08-16. 로컬 탐색 단계 — 커밋·PR·Jira 없음. 계획서 §14 양식.

## work_unit: LOCAL-1 (순수 조회 baseline 분리)

- objective: read-path 부하 프로필에서만 exposure_log·serving_request_metric 동기 쓰기를 끈다.
- AI에게 맡긴 작업: `publication.exposure.enabled`/`publication.request-metric.enabled` if-guard 구현, 토글 테스트(disabled→save 미호출, 기본 프로필 불변).
- 본인이 직접 판단한 내용: (미결) 없음 — 기본값 true 유지가 제품 계약이라는 전제만 재확인.
- 검증에 사용한 테스트·메트릭: 단위 테스트 + 스모크 실측 — read suite 20초 부하(200 응답 1,251건) 동안 exposure_log 4505→4505, serving_request_metric 4505→4505 불변.
- 채택한 결과: if-guard(조건 빈 기각 — diff 국소화). RequestMetricFilter 는 shouldNotFilter 에서 걸러 래핑 비용까지 제거.
- commit 대상 여부: 미정 (단계 B에서 재검증 후)

## work_unit: LOCAL-2 (Publication 관측성)

- objective: /actuator/prometheus 노출, Caffeine·DB loader·L2 커스텀 메트릭, 인스턴스 태그.
- AI에게 맡긴 작업: micrometer-registry-prometheus 배선, CaffeineCacheMetrics("publication-serve"), Counter publication.cache.db.loads / publication.cache.l2.gets{result} / publication.cache.l2.errors, PrometheusEndpointIntegrationTest(고카디널리티 라벨 부재 검증 포함).
- AI 출력에서 발견한 오류/과잉 설계: ① compose env `MANAGEMENT_METRICS_DISTRIBUTION_PERCENTILES_HISTOGRAM_...=true` 가 relaxed binding 에서 percentiles.histogram(double[])으로 오해석돼 **api 4대 기동 실패** — yaml 전용 토글(PUBLICATION_HTTP_PERCENTILES_HISTOGRAM, 기본 false)로 교체해 복구. ② l2.errors 카운터가 대시보드·collect-result 어디에서도 수집되지 않던 계약 누락 — 패널 11·쿼리 세트에 보강.
- 검증: 전체 테스트 스위트 + Prometheus 실쿼리(인스턴스별 요청 수 분리 확인).
- commit 대상 여부: 미정

## work_unit: LOCAL-3 (다중 인스턴스 로컬 환경)

- objective: nginx LB + api 4대 + 전용 postgres + redis 1GB + prometheus/grafana/exporter 를 루트 스택과 격리 기동.
- AI에게 맡긴 작업: tests/loadtest/publication/ 전체(compose·nginx·prometheus·grafana 프로비저닝·k6 5종·스크립트 3종·README).
- 검증(스모크 실측, RATE 50): 요청 분산 api-1~4 = 1476/1426/1443/1453(균등), Grafana 대시보드 자동 프로비저닝 확인, collect-result 16개 쿼리 저장 0실패, prepare-data 시드 32종 + hot/cold 200 검증 통과.
- commit 대상 여부: 미정

## work_unit: LOCAL-4/5 (Redis L2 · L1+L2)

- objective: 캐시 4모드(none|caffeine|redis|two-level)를 설정만으로 전환.
- AI에게 맡긴 작업: cache 패키지 8클래스 + 테스트 38건(동시 미스 single-flight, 코덱 왕복, L1<L2 fail-loud, Redis 장애 fallback 시 DB 1회 합류, 기본 모드에서 Redis 빈 부재).
- 본인이 직접 판단할 내용(미결): 허용 스테일 시간, 차단 상태 fail-open/fail-closed — 스모크는 기본값으로 진행, **본 측정 전 결정 필요**.
- 검증: 신규 38 + 기존 스위트 전체 green(기존 ExplanationStoreCacheTest 4건 무수정 통과), 4모드 스모크 각 1회 관통 — caffeine p99 9.4ms / none 10.8ms / redis 8.7ms(keyspace_hits 0→1220, 키 `publication:v1:{ticker}:latest`) / two-level 7.6ms. 수치는 RATE 50 스모크라 비교 의미 없음(본 측정은 매트릭스로).
- 채택하지 않은 것: 분산 락·Pub/Sub 무효화(계획서 §17), Redis 도입 결론(ADR-0051 결정 6 보류 유지 — 테스트로 경계 고정).
- commit 대상 여부: 미정

## work_unit: LOCAL-0 (실험 계약 확정 — 2026-08-16 사용자 결정)

- **허용 스테일 SLO = 5초** (변경 축: 새 게시·정정의 전 인스턴스 반영 상한).
- **Redis 장애 정책 = fail-open.** 논증: 단순 가용성/안전성 이분법이 아니라 "fail-open을 택해도 스테일 상한은 수학적으로 보장된다" —
  - 차단(serving_scope)은 캐시 표면 밖이다. ExplanationService.serve() 가 게시분 조회 앞단에서 요청마다 DB 를 직접 판정하므로 차단 스테일 = 0, Redis 상태 무관. fail-open 의 위험 표면에 차단이 애초에 없다.
  - 캐시 스테일(게시 스냅샷)의 상한: caffeine=L1 / redis=L2 / two-level 정상=L1+L2(가산) / two-level+Redis 장애=L1 뿐(L2 skip→DB 직행). **장애가 상한을 늘리지 않고 오히려 줄인다** — fail-open 이 안전한 이유가 여기 있다.
- **귀결(TTL 제약)**: two-level 기본 조합 L1 3s+L2 10s 는 가산 상한 13s > SLO 5s → 본 측정의 two-level 계열(T1·E2~E6)은 L1+L2 ≤ 5s 조합만 유효(기본 후보 L1 2s+L2 3s, cold start 완충 우선 시 L1 1s+L2 4s 병행). 코드의 L1<L2 fail-loud 검증과 양립.
- **E6 판정 기준 확정**: 측정된 최대 스테일(새 게시 반영 시간) ≤ 5s. 차단은 즉시 반영이 기대값(캐시 밖 경로 확인용 대조).
- 본인이 직접 판단한 내용: 위 SLO·정책 전부(계획서 §14 "직접 판단할 범위").
- AI 검증: 논증의 코드 근거(차단이 캐시 앞단 매 요청 DB 조회)를 ExplanationService.java 에서 확인, two-level 가산 상한(L2 값 나이 + L1 연장)을 도출해 기본 TTL 과 SLO 의 충돌을 표면화.

## 본 측정 캠페인 구조 (Phase 구분)

계획서 §16 실행 순서를 실행 단위(Phase)로 묶었다. suite 정의는 README·run-matrix.sh --help 의 표가 정본.

| Phase | 내용 | 계획서 근거 | 상태 |
|---|---|---|---|
| A | 무릎점 스윕: B0·B1 rate 50→10,000 (+이등분 2000~2800) | §9 무릎점 먼저 | 완료 — B0 무릎 1600~2000, B1 무릎 2800~3200·포화 실효 ~5.7k rps |
| B | 고정 rate 1600 비교: B0/B1/C1(1·4대)/R1/T1 × 3m × 3회, L1 2s·L2 3s | §16 4~7단계, §4 3회 반복 | 완료 — 판정은 아래 |
| C | 이벤트: E1(콜드스타트 ×caffeine/redis/two-level)·E2/E3(동시만료·jitter)·E4(스케일아웃)·E5(Redis 장애)·E6(게시·차단), rate 1600 ×3회 | §16 8단계 | 진행 중 |
| D | F1 full-path(동기 쓰기 재활성) ×3회 — 최종 후보 모드 | 매트릭스 F1 | 대기 |

Phase B 요약(중앙값): 전 모드 p99 1~2ms 동일(지연 축 무차이) — 캐시 효과는 DB loader 축에만 있다. none 287,998회 / caffeine 4대 1,062회(이론치 0.98x — **스탬피드 없음 수치 확정**, 인스턴스 수 비례) / redis 192회 / two-level 146회(0.81x — L1 이 L2 재적재 흡수). 부하 키 3종(hot 1 + cold 2, k6 기본) 전 구성 동일 조건. 각 구성 r1 은 재기동 콜드로 오염 — 중앙값 사용. 집계 도구: `scripts/summarize.py`.

## Phase C 판독 (진행 중 — E1·E2 완료분)

- **E1 콜드스타트, 가설 반전**: 첫 30s DB loader — caffeine 92 / **redis 1,658(피크 85/s, 요청 수 비례 = 스탬피드)** / two-level 17. "L2 가 콜드 피크를 줄인다" 가설 기각·역전 — redis 단독 경로에는 in-process single-flight 가 없어(Caffeine `get(key, loader)` 의 원자 로더 부재) 첫 로딩 지연 동안 1600 rps 가 DB 직행. 콜드 피크의 1차 방어 = L1 코얼레싱, L2 는 그 뒤에서 인스턴스 수 배수 제거. 콜드 JVM 탓 p99 상승(0.8~1.7s)은 3모드 공통이라 모드 간 차이만 캐시 신호로 읽음.
- **E2 동시 만료(two-level), 스탬피드 기각**: 버스트 9.6만 요청의 온셋 10s DB loader 1.5~1.8건. L1 miss 8~9(키1×4대×TTL주기 이론치) → L2 가 1.5~1.8로 접음(공유 TTL 3s 이론치 일치). 요청 수 비례 아님 — §17 용어 규율상 "스탬피드" 아님.
- **E3 jitter(2s) vs E2(0): 구별 불가 — "TTL 계층+jitter 피크 완화" 가설, 이 시스템에선 무의미로 판정.** 온셋·버스트 전체·피크 rate·변동계수 전부 반복 편차 안(버스트 60s DB loader 22 vs 20, 폭 겹침). 원인: 완화할 피크가 없음 — jitter 의 분산 대상인 L2 재적재가 60s 에 20건뿐. **사정거리 제한**: 단일 키 시나리오라 "다종 키 동시 만료" 국면은 안 잼 — 그 국면 판정엔 별도 시나리오 필요하나, L1 코얼레싱이 1차 방어로 동일 작동하므로 jitter 도입 근거는 약함. 계측 해상도(rate[30s]·step 5s) 평활 주의 — 단 스탬피드는 자릿수(E1 redis 1.5k~2.6k)로 나타나 판정 불변.
- **E4 스케일아웃(two-level): "Redis 가 scale-out cold start 에 필요" 가설 지지.** api-4 합류 후 그 L1 미스 1.5/s 가 L2 hit 로 1:1 흡수, 클러스터 DB loader 평평(합류 전 22.0~24.8 → 후 22.5~24.5/30s). caffeine 단독 가정 시 DB loader ~3배 상당분. E1 과 종합: **스파이크 방어=L1 코얼레싱, 증설분 상시 상환=L2** — 단 hot-key 3키라 흡수분은 콜드필이 아닌 TTL 재적재분(실운영 다키 워킹셋에선 L2 효과가 커질 방향, 즉 과소평가). 502 노이즈 0~0.08%(계약된 nginx 4대 고정 재시도), k6 exit 99 는 502 1건에도 깨지는 체크 임계 탓. 큰 dropped(r2·r3 5~6.7만)는 run 개시 60s(api-4 정지+재시도)의 산물 — 3대 정상 구간은 1,600 rps 만근·p99 1.0ms.
- **E5 Redis 장애(two-level): fail-open 논증 실측 통과.** ① 장애 60s 오류 0·1,600 rps 평탄(가용성 무손실) ② DB loader 5.15/s = L1 단독 이론치(키3/TTL2s×4대=6/s) 수준 — 요청 수 비례(1,600/s) 대비 310배 낮음, 평시 대비 6배 개방에 그침. LOCAL-0 "fail-open 이어도 상한 보장" 전제 실증. 메커니즘: L2 errors 가 loader 의 정확히 2배(get 실패+put 실패), 장애 중 p99 313~319ms = 2×command timeout 에서 유계·연쇄 악화 없음. 복귀: 재적재 herd 없음(L2 miss ≤0.76/s < 평시 0.85/s), start 후 ~10s 첫 hit·완전 정착은 관측 창 밖(외삽 35~40s — 정확값 필요 시 측정 연장).
- **F1 full-path(r2·r3, 1600 rps 만근·오류 0): 쓰기 대가는 모드 독립, 지연 꼬리는 아님.** 요청당 xact 정수 분해 — 기저 3.00(모드 불변, serving_scope 캐시-밖 성질과 부합) + 캐시 없으면 +1(loader) + 쓰기 켜면 +2(exposure+metric). 쓰기 증분 +3,189~3,212/s = 이론 3,200/s 와 0.4% 일치, 모드 간 차 0.7% — **캐시는 쓰기 병목을 못 가린다(full 경로 요청당 5건 중 read 1건만 지움)**. 지연: full 에서 caffeine p99 +2.6ms vs two-level +7.2ms(p95 증분 ~3배), 원인 후보 = 쓰기가 커넥션 잡은 상태에서 L2 왕복 겹침(hikari pending 337 간헐 스파이크, two-level r3 만) — **방향만 신뢰, 크기 확정엔 반복 부족(모드당 깨끗한 rep 2개)**. 처리량 상한 이동은 미측정 — full 무릎 스윕 필요(산술 추정 read 의 ~60%).
- r1 콜드 오염의 원인 확정 보강: E3(E2 직후 워밍된 스택에서 시작)는 r1 도 깨끗 — 오염은 컨테이너 재기동 직후 효과.
- 분석 도구: summarize.py(비교표) + 스크래치패드 e_analysis.py(시계열, 스크래치패드라 휘발 — 승격 시 scripts/ 로 이동 필요).

## 본 측정 이슈 로그

- **E6 3반복 전부 무효(재실행 필요)**: ① k6 setup 컨텍스트에 `__ITER` 부재 — lib.js headers() 가 setup 에서 ReferenceError → exit 107, 요청 0건(setup 에서 HTTP 를 쓰는 publication-change 만 해당). ② k6 즉사 후에도 백그라운드 서브셸이 --new-snapshot·--block 을 실행해 DB 를 오염시켰고, reset-scope 가 rep 루프 밖이라 r2·r3 는 어차피 차단 상태에서 시작(baseline null→전부 fresh, 측정 불성립). 패치 2건(lib.js __ITER 가드·E6 rep 루프 내 reset-scope)은 Phase D 종료 후 적용 — 실행 중 스크립트 편집 위험 회피. E6 잔재 차단은 run-matrix 의 시드 reset-scope 가 다음 invocation 에서 자동 복구(F1 시드 통과로 확인).
- **suite 간 scope 잔재**: E6 의 --block 이 다음 suite 시드의 200 검증을 (정당하게) 실패시킴 — run-matrix 시드 호출에 --reset-scope 상시화로 수정 완료.

- **prepare-data 멱등성 버그(수정 완료)**: policy_version 조건부 INSERT 가 `WHERE NOT EXISTS` 였는데, WHERE 는 집계(max) *입력*만 거르고 집계 결과 행(0행 입력→1행 출력)은 못 걸러 2회째 호출에서 `uq_policy_version_no` 충돌 → B1 스윕 중단. `HAVING NOT EXISTS` 로 교체, 2회 연속 실행으로 멱등성 재검증. 스모크에서 못 잡은 이유: DB 리셋 후 1회씩만 호출돼 재호출 경로가 안 밟혔다.
- **B0 무릎점 부재**: 캐시 없음 1대가 rate 1600 까지 drop 0·p99 1.2ms — 예상보다 순수 조회가 훨씬 빠름(hot-key, warm DB, read suite). 스윕을 목표 부하 10,000 까지 연장.

## Phase C 완결 (E6 재실행) · Phase D · 캠페인 종료

- **E6 재실행(패치 후) 3반복 유효·판정 통과**: 새 게시 반영 원시 4.22~4.45s(주입 지연 ~0.8s 보정 시 ~3.5s) — **SLO 5s·가산 상한(L1 2s+L2 3s) 안**. 차단 스테일 **0.00~0.01s**(마지막 200과 첫 204가 붙어 있음) — LOCAL-0 "차단은 캐시 밖, 스테일 0" 논증 실측 완결. 전환 폭 0.06~0.28s(인스턴스 간 다른 본을 보는 창 사실상 없음). **주의**: 3반복이 TTL 위상 고정(jitter 0·결정적 적재)이라 독립 샘플 아님 — 최악 위상(만료 직전 쓰기) 미실측, "최대 스테일 실측" 주장에는 EVENT_AT 위상 흔들기 변형 필요. 5s 보장의 근거는 여전히 가산 TTL 논증이고 측정은 무모순 확인.
- 최종 매트릭스: `python3 scripts/summarize.py` 가 정본(16구성). **E 계열의 실측 RPS·이론치 배율 열은 분모(3m 고정) 불일치로 무의미** — E 판정은 시계열 판독(위 Phase C 절)이 정본. summarize.py 는 요청 0건 run 스킵 처리 추가됨.
- 분석 스크립트 승격: e_analysis.py·e6_timeline.py 를 scripts/ 로 복사(재현성 — §4 "모든 숫자에 실행 명령 연결").
- 스택 정리: `down -v` 완료 (2026-08-16 12:45 KST 경).

## 결정 프레임 (§12 대조 — 결정 자체는 사용자 몫)

- **Redis only: 탈락.** E1 콜드 스탬피드(요청 수 비례 1,658건/30s — in-process single-flight 부재). §12 조건 "장애 정책 감당" 이전에 콜드 안전성에서 실격.
- **Caffeine only 조건 충족 현황**: loader 인스턴스 수 수준 ✓(1,062/3m, 이론치 0.98x) · cold start 피크 허용 ✓(스파이크 없음 — L1 코얼레싱이 방어) · TTL 불일치 허용 ✓(caffeine 단독 상한 = L1 2s < SLO 5s, 차단 0) · full-path 지연 꼬리 우위 ✓(p99 4.2ms vs two-level 5.7~11.5ms) · ADR-0051 결정 6(Redis 반입 보류)과 정합.
- **two-level 의 실증 이득**: E4 scale-out 상시 상환(합류 인스턴스 미스 L2 1:1 흡수, 실운영 다키 워킹셋에선 커질 방향)·E5 장애 시에도 L1 등가로 유계(fail-open 안전). 대가: 운영 복잡도·장애 의존성 추가·쓰기 부하 겹칠 때 풀 대기 꼬리(방향 확정, 크기 미확정).
- 수치 우위만 보면 **Caffeine only 로 기움** — two-level 의 남은 근거는 "실운영 워킹셋의 scale-out/cold-fill" 하나이고, 이는 로컬 hot-key 3키 실험이 과소평가하는 축이라 결정 기록(ADR/글3)에서 명시 필요.
- **판정의 사정거리와 재검토 트리거 (단정 방지)**: 이 판정은 "소수 hot-key·~1ms 로더·4대·전용 PG" 조건부다. 이번 실험은 Redis 의 정통 근거 3축 — ① 워킹셋 > 인스턴스 메모리 ② 비싼 로더(100ms+: 집계·외부 API·재계산) ③ 많은 인스턴스(수십 대)·잦은 증설 — 을 하나도 밟지 않았다(각각 키 3종·1ms·4대). 유니버스 수백 종 확대, 로더 고비용화, 인스턴스 수십 대, 공유 관리형 DB 전환 중 하나라도 성립하면 재검토. 또한 E1 "redis 단독 스탬피드"는 Redis 결함이 아니라 **single-flight 없는 우리 구현**의 성질 — L2 앞에 로컬 코얼레싱만 넣어도 사라지므로, redis 단독 탈락의 정확한 서술은 "single-flight 없는 redis 단독" 탈락이다. 캐시 외 용도(세션·rate limit·락)가 생기면 반입 비용이 선지불되어 L2 한계비용이 0 에 수렴하는 것도 재검토 사유.

## 후속 논의 (2026-08-16 오후 — 측정 종료 후 해석)

### Redis 필요 조건 일반론 (글1·글3 뼈대)

캐시 용도로 Redis 가 정당해지는 조건 5: ① 워킹셋 > 인스턴스 메모리 ② 비싼 로더(100ms+: 집계·외부 API·재계산) ③ 인스턴스 다수·잦은 증설(중복 로딩·cold-fill ∝ 대수×배포빈도) ④ DB 가 약하거나 공유 자원 ⑤ 값을 여러 서비스가 공유. +캐시 외 용도(rate limit·세션·락·큐)가 생기면 반입 비용 선지불로 L2 한계비용 0. Redis 로 해결 안 되는 것: DB↔캐시 dual-write 정합성 / 스탬피드(코얼레싱의 몫 — E1 실증) / L1 있는 한 인스턴스 간 불일치.

**한 줄 판별식**: "같은 값을 (여러 프로세스가) × (비싸게) × (많이) 다시 만들고 있는가" — 둘 이상 예면 Redis 정당, 전부 아니오면 L1. 현재 시스템은 셋 다 아니오(4프로세스·1ms 로더·키 3종).

### 규모 산술 — 토스급 대조 (외삽 아님, 산술 예시)

공개 자료 기준 토스급 피크 = 초당 수만~수십만 건(포인트 지급 API "초당 수십만 건", toss.tech monitoring-traffic). 우리 실측 코어당 ~1,600 rps(경량 조회+캐시 히트 99.6% 조건) 기준: 10k rps ≈ 7코어, 100k ≈ 63코어(8코어 인스턴스 헤드룸 2배 시 16~20대), 300k ≈ 50대. **주의**: 토스 워크로드(인증·원장 결합)는 코어당 처리량이 수 배~수십 배 낮을 것 — 산술은 규모 감각용. 시사점: 수십 대 규모 = 재검토 트리거 ③ 성립 — "토스라면 Redis"와 "우리 4대는 Caffeine"은 같은 판별식의 양 끝.

### 100,000 req/s 로컬 테스트 판정: 풀스택 불가

실측 포화 5.7k 의 17.5배·서버만 ~63코어 필요 + k6 동일 호스트 → §9 규율상 유효 측정 불성립(발생기 병목). 가능한 대안: ① redis-benchmark 단품 100k ops 검증(로컬 가능, "Redis 처리량 축" 실측용 — 미실행) ② 코어당 실측 기반 스케일 모델(완료, 위 표) ③ 클라우드 분산 부하(서버 플릿+분산 k6 — 단계 B 이후 별도 결정). 이력서·글에는 "로컬 M5 Pro Compose 에서 최대 ~5.7k rps 까지 검증"으로 표기(§9 문구 규율).

### 이력서 문단 검증

실험 전 초안("Redis 가 병목 → L1 추가" 서사)은 실측과 불일치(Redis 병목 미관측·p99 개선 없음·10k 미달·jitter 무효)로 **현 데이터로는 기재 불가** — §15·§17 규율 위반. 실측 기반 재작성본은 `~/Downloads/portfolio-cache-paragraph-v2.md`. 원 서사를 살리려면 R1/T1 고부하(3~6k) 스윕으로 "Redis 단독 무릎이 먼저 오는지" 실측 필요(미실행 — 로컬은 호스트 CPU 선포화 가능성 있음).

## 경계 실측 캠페인 (2026-08-17 — 워킹셋 스윕 W + 핫키 스파이크 S, 29 run)

### W — L1 붕괴 무릎과 "L1은 죽고 L2는 사는 창" (rate 1,600·균등 접근·L1 2s·L2 3s)

| N | L1 hit | caffeine DB loader(3m) | two-level DB loader | L2 상환 배율 | two-level p99 |
|---:|---:|---:|---:|---:|---:|
| 100 | 88.8% | 20,933 | 3,393 | 6.2× | 1.7ms |
| 300 | 72.6% | 51,183 | 10,124 | 5.1× | 2.1ms |
| **800** | **50.0%** | 144,199 | 37,873 | 3.8× | 1.8ms |
| **1,088(실제)** | **42.4%** | **165,825** | **49,500** | **3.3×** | 2.0ms |
| 3,000 | 21.0% | 147,037(2m) | 69,951(2m) | 2.1× | 6.3ms |
| 5,000 | 13.7% | 160,271(2m) | 93,192(2m) | 1.7× | **11.9ms** |

- **L1 붕괴 무릎(hit 50%) 실측 = N 798~800** — 사전 등록 예측(rate×TTL/(4대)=800)과 일치. 저 N 에선 선형 모델 정확(N=100: 예측 87.5 vs 실측 88.8), 고 N 은 포아송 도달로 완만한 붕괴.
- **실제 유니버스 1,088종은 무릎 너머**(hit 42%, caffeine 이면 DB ~920/s) — 전체 유니버스 균등 서빙 시 two-level 이 3.3× 상환. **재검토 트리거 "수백 종" → 실측 좌표 "N≈800(균등 기준)"으로 승격 — 글3 표 갱신 완료, 글4(`_posts/2026-08-17-measuring-the-cache-boundary.md`) 집필 완료(2026-08-17, 수치 감사 통과, 미커밋). 블로그 4부작 완성.**
- L2 도 유한: 상환 배율 6.2×→1.7× 감쇠(L2 유효 상한 예측 ~4,800 부합, N=5,000 L2 hit 41%). **대워킹셋 역효과**: two-level p99 가 N=5,000 에서 11.9ms(caffeine 2.4ms) — miss 마다 L2 왕복이 추가되는 비용이 미스율과 곱해짐.
- **W-3 현실 대조(1088 + hot 90%)**: L1 hit 90.5%, caffeine loader 27,241(≈151/s) — **현실 분포에선 무릎이 사실상 사라짐**. 균등 접근이 최악 케이스라는 한정과 함께 인용할 것.
- 집계 함정: w_analysis 의 N=1088 "반복 5" 는 W-1(2m)·W-3(hot90) 혼입 — 확정치는 3m×3회 run 별 값(위 표)으로 인용. rate 필터(1600)는 반영됨.

### S — 핫키 스파이크 (200→4,000 rps 10초 램프, hot 99%)

도착률 **20~22배** 급증에 DB loader 는 caffeine 3.3→5.8/s(**1.7배**), two-level 0.7→0.9/s(1.3배) — 온셋 10s 총량 42~45건, 오류·dropped 0, p99 2.7~3.0ms. **가설 지지: 급변 순간이 캐시의 최악이 아니라 최선** — 쏠림일수록 single-flight 뒤로 몰려 loader 상한은 키 수×TTL 로 잡힘. EDGE 존재 이유(가격 급변→설명 조회 폭주)가 발동하는 순간의 안전성 실측.

### 결정에의 함의

Caffeine only 결정은 **현재 서빙 형태(소수 종목 노출·핫키 집중)에선 그대로 유효** — S 와 W-3 이 지지. 단 **"전체 유니버스(1,088종)를 균등에 가깝게 서빙"하는 제품 변화가 오면 N≈800 무릎을 넘어 two-level 재검토가 정당**해진다 — 이제 그 선이 실측 좌표다. 글3 트리거 표의 "워킹셋 수백 종" 항을 "N≈800(균등 최악 기준, 현실 분포면 완화)"로 갱신 가능.

## 미결·다음 단계

1. ~~LOCAL-0 제품 판단 2건~~ → 확정. ~~본 측정~~ → 완료(B0~F1 전 suite, E6 재실행 포함).
2. ~~캐시 전략 최종 결정~~ → **Caffeine only 확정 (2026-08-17 사용자 결정)**. 기각: redis 단독(콜드 스탬피드·single-flight 부재), two-level(편익 초당 1.5회 vs full-path 꼬리·운영 복잡도·ADR-0051 번복 비용). 재검토 트리거는 결정 프레임 절 그대로 유효. 남은 산출: ADR 또는 설계 문서(단계 B에서), 글3.
3. 선택적 추가 측정: full-path 무릎 스윕(상한 이동 실측), E6 위상 흔들기(최대 스테일 실측), two-level 꼬리 반복 보강, 다키 워킹셋 시나리오.
4. ~~글2·글3~~ → 초안 완료(2026-08-17, 미커밋): 글2 `the-redis-experiment-that-removed-redis.md`(2,107단어), 글3 `four-instances-caffeine-was-simpler.md`(674단어, 결정 기록) — 둘 다 수치 실측 대조·형식 감사 통과. **블로그 3부작(개념→실험→결정) 완성.** 포트폴리오 문단 정본화만 남음(재작성본 `~/Downloads/portfolio-cache-paragraph-v2.md`).
5. 단계 B 승격: Jira 티켓·worktree·재검증·커밋 (기존 계획대로).
2. 본 측정: `scripts/run-matrix.sh --suite B0..F1 --repeat 3` (무릎점 스윕 먼저), 글2·글3.
3. 글1 초안 완료: `choyoungseo20.github.io/_posts/2026-08-16-multi-instance-does-not-require-redis.md` (1,986단어, 실측 수치 없음 — 형식 검수 통과).
4. E4(scale-out)·E5(Redis 장애)·E6(게시·차단) 시나리오는 스크립트만 검증(bash -n) — 실런 미수행.
