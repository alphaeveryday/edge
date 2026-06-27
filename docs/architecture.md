# 시스템 아키텍처

이 문서는 **시스템(소프트웨어) 아키텍처** — 컴포넌트, 상호작용, 데이터 흐름, 신뢰 경계 — 를 다룬다.
정적인 폴더·역할 목록은 [README.md](../README.md)가, 결정의 배경은 [ADR](adr/)이 SSOT다.
클라우드/배포 토폴로지는 이 문서의 범위 밖이다(§6).

## 1. 개요
멀티테넌트 시스템이다. 고객사(테넌트)는 자기 사이트에 **임베드 위젯**을 띄우고,
별도 **테넌트 콘솔**에서 자기 테넌트의 설정·데이터를 관리한다(테넌트 내부 범위).
그 위에 플랫폼 운영자(우리)가 **모든 테넌트를 가로질러** 관리하는 **super-admin 콘솔**(cross-tenant)이 있다 — [[adr/0008-super-admin-console]].
백그라운드에서 데이터가 적재되고 분석된다.
모든 서비스는 **하나의 공유 DB**를 통해 결합한다 — 서비스 간 직접 호출이 아니라 DB가 통합 지점이다.

## 2. 컴포넌트와 책임
역할 요약은 [README.md](../README.md)에 있다. 여기서는 상호작용 관점에서만 본다.

- **widget-ui** (Node) — 고객 사이트에 임베드되는 위젯. 익명 최종 사용자에게 노출.
- **tenant-console-ui** (Node) — 테넌트 직원이 로그인해 쓰는 관리 콘솔. **한 테넌트 내부 범위**.
- **super-admin-ui** (Node) — 플랫폼 운영자가 쓰는 **cross-tenant** 운영 콘솔. [[adr/0008-super-admin-console]].
- **gateway** (JVM) — 인터넷 트래픽의 단일 엣지. widget·console·admin 트래픽을 모두 받아 라우트별로 필터링·전달.
- **widget-api** (JVM) — 위젯용 백엔드. **읽기 전용·좁은 표면**.
- **tenant-console-api** (JVM) — 콘솔용 백엔드. **읽기/쓰기·넓은 표면**(한 테넌트 범위).
- **super-admin-api** (JVM) — 운영 콘솔용 백엔드. **cross-tenant 읽기/쓰기 = 최고 권한 표면**.
- **data-pipeline** (Python) — 스케줄러로 외부 데이터를 DB에 적재.
- **analysis-engine** (Python) — 스케줄러로 적재 데이터를 분석해 `analysis_result`를 DB에 저장.
- **libs/schema** — DB 스키마 SSOT(마이그레이션 + 생성 모델). [[adr/0005-db-as-contract]].
- **libs/jvm-common** — 공유 도메인 + `analysis_result` 접근 로직.
- **libs/ui-kit** — 두 UI 공유 디자인 시스템. **libs/py-common** — Python 공통 유틸.

## 3. 통신·데이터 흐름

**동기(요청/응답)**
- 외부: `최종 사용자 → widget-ui → gateway(widget 라우트) → widget-api → DB(읽기)`
- 콘솔: `테넌트 직원 → tenant-console-ui → gateway(console 라우트) → tenant-console-api → DB(읽기/쓰기, 한 테넌트)`
- 운영: `운영자 → super-admin-ui → gateway(admin 라우트) → super-admin-api → DB(읽기/쓰기, cross-tenant)`

**배치(스케줄러 트리거)**
- `스케줄러 → data-pipeline → DB(적재)`
- `스케줄러 → analysis-engine → DB에서 적재분 읽기 → analysis_result 쓰기`

**DB를 통한 통합(핵심)**
서비스끼리 서로를 직접 호출하지 않는다. analysis-engine이 만든 `analysis_result`를
API가 (jvm-common을 통해) 읽는 식으로, **결합은 항상 DB를 거친다**.
그래서 DB 스키마가 사실상 서비스 간 계약이며, 변경 절차를 엄격히 둔다 — [schema.md](schema.md), [[adr/0005-db-as-contract]].

## 4. 신뢰 경계

"외부 vs 내부"는 *사설망 여부*가 아니라 **노출 표면과 권한**의 구분이다.
widget도 console도 인터넷을 통해 접근되지만, 신뢰 수준과 허용 동작이 다르다.

**단일 엣지, 라우트별 격리**
gateway가 **widget·console·admin 트래픽을 모두** 앞단에서 받되, 호스트/경로로 나누고
**라우트별 독립 필터 체인(fail-closed)** 을 적용한다:

| | widget 라우트 | console 라우트 | admin 라우트 |
|---|---|---|---|
| 사용자 | 익명 최종 사용자 | 인증된 테넌트 직원 | 인증된 플랫폼 운영자 |
| 범위 | 공개 | 한 테넌트 내부 | cross-tenant(전역) |
| 인증 | 없음/위젯 토큰 | 테넌트 사용자 세션 | 운영자 세션 |
| 허용 메서드 | 읽기(GET) 위주 | 전체(읽기/쓰기) | 전체(읽기/쓰기) |
| 레이트리밋 | 공격적(고트래픽·익명) | 사용자/테넌트 단위 | 운영자 단위 |
| 망 노출 | 공개 인터넷 | 공개 인터넷 | **VPN/IP allowlist 제한** |
| 기타 | 임베드용 CORS | CSRF 등 | CSRF 등 |

admin은 **최고 권한(cross-tenant)** 표면이지만 운영자는 **소수·알려진 집합**이라, 테넌트 직원과 달리 공개 인터넷 노출이 필요 없다 → 엣지 정책에 더해 **망 수준 제한**을 건다([[adr/0008-super-admin-console]]).

**서비스 레벨 방어(엣지에만 의존하지 않음)**
경계의 무결성은 gateway 설정 하나가 아니라 **백엔드 서비스 분리**에서도 나온다:
- `widget-api`는 **읽기 전용**이라, 설령 잘못 라우팅돼도 데이터를 변경할 수 없다.
- `tenant-console-api`는 서비스 레벨에서도 인증을 요구한다(엣지 통과만으로 접근 불가).
- `super-admin-api`는 **별도 서비스**다 — cross-tenant 권한을 `tenant-console-api`에 섞지 않는다(테넌트 직원 세션이 닿는 표면에 전역 권한이 생기면 테넌트 격리가 깨진다). 서비스 레벨에서도 운영자 인증·인가를 요구한다.
- 따라서 gateway 필터 오설정은 **단일 실패점이 아니다** — 서비스 표면 제한이 백스톱.

**노출 없는 내부 작업**
`data-pipeline`·`analysis-engine`은 스케줄러로만 동작하며 gateway/인터넷에 노출되지 않는다.

## 5. 다이어그램

```
                              인터넷                          │ VPN/IP 제한
   ┌──────────────────────────────────────────────┐         │
   │  익명 최종 사용자        테넌트 직원              │  플랫폼 운영자
   │       │                    │                   │      │
   │   widget-ui          tenant-console-ui         │  super-admin-ui
   └───────┼────────────────────┼───────────────────┴──────┼──────
           │                    │                          │
        ┌──┴────────────────────┴──────────────────────────┴──┐
        │                     gateway                         │  ← 단일 엣지, 라우트별 필터(fail-closed)
        │   [widget 체인]    [console 체인]    [admin 체인]      │
        └──┬────────────────────┬──────────────────────────┬──┘
           │                    │                          │
       widget-api        tenant-console-api          super-admin-api
       (읽기 전용)          (읽기/쓰기, 한 테넌트)        (읽기/쓰기, cross-tenant)
           │                    │                          │
           └────────────────────┼──────────────────────────┘
                                 │
                              ┌──┴──┐
                              │  DB │ ◀── data-pipeline (적재)   ◀─ 스케줄러
                              └──┬──┘ ◀── analysis-engine (분석)  ◀─ 스케줄러
                                 │
                           libs/schema = 계약(SSOT)
```

## 6. 범위 밖
- **클라우드/배포 토폴로지** — 리전, 네트워크, 매니지드 서비스, 스케일링, 시크릿 관리 인프라. 인프라가 정해지면 별도 문서/ADR로 다룬다.
- **CD(지속적 배포)** — 인프라 확정 후 설계. "main 머지 = 전체 자동 배포"는 두지 않으며, 마이그레이션 확장 단계를 코드 배포보다 먼저 실행하는 순서가 핵심이 된다.
