# edgesignal

증권사에 납품하는 B2B 산출물. 엔진의 분석 결과를 받아 검수·정책·게시를 거쳐 MTS에 서빙한다.

## 모듈별 배포 플레인

배포 위치는 디렉터리가 아니라 이 표가 담당한다([ADR-0061](../../docs/adr/0061-product-axis-directory-layout.md)).

| 모듈 | 런타임 | 플레인 | 역할 |
|---|---|---|---|
| tenant-sync-api | JVM | cloud | Sync Agent Pull 표면 |
| super-admin-api | JVM | cloud | 플랫폼 운영자 API |
| super-admin-ui | Node | cloud | 플랫폼 운영자 콘솔 |
| sync-agent | JVM | onprem | DMZ의 Cloud Pull과 체크섬 검증 |
| intake | JVM | onprem | 내부망 Raw Event Store 적재 |
| screening-worker | JVM | onprem | 정책 평가와 상태 분기 |
| publication-api | JVM | onprem | MTS 조회 표면 |
| tenant-console-api | JVM | onprem | 테넌트 검수·정책 API |
| tenant-console-ui | Node | onprem | 테넌트 검수·정책 콘솔 |

- cloud: 벤더 운영 환경
- onprem: 증권사 통제 환경. 증권사 명의 클라우드 계정 포함([ADR-0010](../../docs/adr/0010-hybrid-onprem-pivot.md))

모듈 역할의 상세는 [docs/repo-structure.md](../../docs/repo-structure.md)가 정본이다.
