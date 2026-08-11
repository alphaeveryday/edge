# ADR-0036: Sync 온프렘 토폴로지 — Sync Agent(DMZ)+Intake(내부망) 2모듈 표준

- 상태: 승인됨 (조율 메커니즘 — DMZ-local 마크·commit-ack — 은 [0052](0052-sync-two-module-standard-reaffirmed.md) 결정 5가 대체)
- 날짜: 2026-07-21

## 맥락
온프렘 Sync는 벤더 Tenant Sync API를 outbound Pull해 온프렘 DB에 적재한다. 지금까지 context.md §3은 **단일 Sync Agent**(DMZ 배치, Pull·검증·내부망 DB 적재를 한 모듈이 수행)를 기본으로 두고, DMZ→내부망 직접 DB 커넥션을 금지하고 망연계(망간자료전송) 경유를 요구하는 증권사만을 위한 **2단 구성**(DMZ는 Pull·검증까지, 내부망 수신 모듈이 저장)을 옵션으로 규정했다([ADR-0021](0021-design-reinforcement.md)의 DMZ 배치 확정 위에).

문제는 단일 Sync Agent가 **두 개의 서로 다른 망 구역을 한 모듈로 걸친다**는 점이다: 외부와 닿는 Pull(DMZ)과 데이터를 눕히는 저장(내부망 DB 접근)은 신뢰 수준·공격 표면·장애/스케일 프로파일이 다르다. 단일 모듈은 DMZ에 있으면서 내부망 DB로의 직접 커넥션을 전제하는데, 이는 다수 증권사의 망분리 정책과 충돌하고 2단 구성을 "예외 경로"로 밀어낸다.

## 결정
**Sync Agent(DMZ) + Intake(내부망) 2모듈 상시 분리를 표준 토폴로지로 삼는다. 단일 모듈 구성은 옵션으로 강등한다.**

- **Sync Agent** — DMZ 배치. Tenant Sync API를 outbound Pull(mTLS), 번들 무결성(SHA-256 체크섬) 검증까지 수행한다. **내부망 DB에 직접 접근하지 않는다.** 외부와 닿는 유일한 컴포넌트. (기존 SSOT·코드가 쓰는 이름을 유지 — 아키텍처 뷰의 "Relay Worker"가 이 모듈에 대응한다.)
- **Intake** — 내부망 배치. Sync Agent가 검증한 번들을 넘겨받아 Raw Event Store에 멱등 적재한다. 외부 통신이 없다. (아키텍처 뷰의 "Intake Worker"에 대응.)
- Sync Agent→Intake 전달은 증권사의 승인된 DMZ→내부망 경계 메커니즘(망연계 솔루션 또는 통제된 내부 전송 채널)을 사용한다. 이 경계가 곧 망분리 지점이다.
- **상태 소유 = Intake(내부망)**. 권위 상태 — 수신 번들(`received_bundle`)과 committed cursor(`sync_state`) — 는 **Intake가 온프렘 DB에 기록**한다. Sync Agent(DMZ)는 내부망 DB에 못 닿으므로 이 durable 상태를 소유하지 않는다.
- **cursor 재개(유실 방지)**: Pull 재개점은 **Intake의 committed cursor가 권위**다. Sync Agent는 DMZ-local 진행 마크(내부망 DB 아님)를 두되, **Intake가 commit-ack한 cursor까지만 전진**시킨다 — commit-ack 없이 앞서지 않는다. 이 commit-ack(또는 committed-cursor 조회)은 **내부망→DMZ 제어 신호**로 온프렘 내부 채널을 쓴다(망분리 제약은 **외부(인터넷) outbound**에만 적용되고, 내부 DMZ↔내부망 통제 신호는 허용). 이렇게 묶으면 전송·Intake commit 실패 시 재개 cursor가 전진하지 않아 해당 번들을 재-Pull하고, 도달한 중복만 멱등 upsert가 dedup한다 — **skip(유실) 없음**. (ack 없이 DMZ 마크를 먼저 전진시키면 실패한 번들을 `?after=`가 건너뛰어 유실되므로, ack는 정확성 필수다.) 단일 모듈 옵션에서는 sync-agent가 committed cursor·번들을 모두 소유해 이 조율이 불필요하다. 계약·도메인 문서([sync-protocol.md](../contracts/sync-protocol.md)·[state-machine.md](../domain/state-machine.md))는 이 결정으로 Pull 주체(Sync Agent)와 durable 저장 주체(Intake)를 분리해 정합화했다. baseline 스키마 `migrations-onprem/…init_onprem_sync_baseline`의 `writer = sync-agent` 주석은 단일 모듈 기준이며, **적용된 마이그레이션은 불변**이므로 2모듈 구현 시 새 마이그레이션으로 Intake writer를 반영한다(온프렘 sync는 walking skeleton 미구현).
- **불변**: Cloud pull 표면은 Tenant Sync API 하나(cursor 기반 delta, mTLS, 번들 체크섬 — [sync-protocol.md](../contracts/sync-protocol.md)), 방향은 outbound Pull only, 목적지는 고정 FQDN:443 단일 화이트리스트. 2모듈 분리는 **온프렘 내부 구조**일 뿐 Cloud 계약·방향·인증서-테넌트 바인딩([sync-auth.md](../contracts/sync-auth.md))을 바꾸지 않는다 — 인증서로 Pull하는 주체는 여전히 DMZ의 Sync Agent다.
- **옵션(단일 모듈)**: DMZ→내부망 직접 DB 커넥션을 허용하고 컴포넌트 최소화를 원하는 증권사는 Sync Agent+Intake를 한 모듈(DMZ 배치, 내부망 DB 직접 적재)로 합쳐 배포할 수 있다. 이는 종전의 단일 Sync Agent 구성과 같다.

## 대안
- **단일 Sync Agent 기본 유지(2단은 옵션)** — 기존 안. 단일 모듈이 DMZ에서 내부망 DB에 직접 커넥션하는 것을 표준으로 전제해, 망연계를 요구하는 다수 증권사에서 매번 예외 설계를 유발. 배제.
- **DMZ 모듈을 "Relay"로 리네임** — 아키텍처 뷰(v0.2)와 이름은 일치하나 `Sync Agent`가 계약(sync-auth·sync-protocol·event-bundle)·코드(`sync-agent` 모듈·tenant-sync-api·migrations)·terraform까지 깊게 박혀 있어 리네임 블라스트 반경이 과도. 이름은 `Sync Agent`를 유지하고 뷰의 "Relay"는 매핑으로 대응. 배제.

## 결과
- context.md §3 네트워크 배치·§4.2 컴포넌트 표(Sync Agent 행 → Sync Agent·Intake)·§5 변경표를 2모듈 기준으로 갱신한다. 단일 모듈은 옵션으로 명시.
- Intake가 하나 늘고 DMZ→내부망 전달 경계가 명시적 배선이 되는 비용을 받아들인다 — 대신 망분리가 구조로 성립하고 망연계 증권사가 특례가 아니라 기본이 된다.
- Intake는 walking skeleton 단계에서 구현된다(현재 미구현, Sync Agent도 미구현). 아키텍처 뷰([../architecture/system-architecture.md](../architecture/system-architecture.md))의 Relay/Intake Worker 논리 단위와 배포 모듈(Sync Agent/Intake)이 이 결정으로 정렬된다.
