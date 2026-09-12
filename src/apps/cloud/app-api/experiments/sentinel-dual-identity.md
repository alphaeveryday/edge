# Sentinel 이중 정체성 사고 — 승격 노드가 자기 자신의 replica 로 강등

2026-09-12, 비교 회전(RESULTS.md) 중 발견·수정·검증. 인프라(Sentinel 설정) 문제이며
앱 코드·Spring 설정과 무관하다.

## 증상

master SIGKILL 실험에서 **monitor 에 hostname 으로 등록된 노드가 승격 대상이 된 2개 런
모두**, 승격 0.9~2초 뒤 새 master 가 replica 로 되돌아가며 앱 쓰기가 전부 거절됐다.
약 30초 뒤 sentinel 이 그 master 를 다시 down 판정하고 2차 페일오버로 자가 수습됐다.
replica-1·replica-2 컨테이너가 승격될 때는 발생하지 않았다.

승격 노드의 로그 (스모킹 건 — 두 줄 간격 0.9초, 둘 다 sentinel 이 보낸 명령):

```
07:55:13.207 * MASTER MODE enabled (user request from 'name=sentinel-…')
07:55:15.106 * REPLICAOF redis-master:6379 enabled (user request from 'name=sentinel-…')
```

`announce-hostnames` 를 제거한 뒤에도 재현됐다 — 이때는 `REPLICAOF 172.24.0.2:6379` 가
**172.24.0.2 자신에게** 전송됐다(laddr 동일). hostname 전달 문제가 아니라 정체성 문제다.

## 원인

Sentinel 은 노드를 **주소 문자열로 식별**한다. monitor 라인의 `redis-master`(hostname)와
INFO 로 발견한 `172.24.0.2`(IP)가 **같은 노드의 두 정체성으로 이중 등록**된다:

```
sentinel 장부:
  redis-master:6379   ← 설정 파일에서 온 이름
  172.24.0.2:6379     ← 운영 중 발견한 IP     (실은 같은 서버)
```

페일오버 절차에서:

1. IP 정체성(`172.24.0.2`)을 골라 승격 — `SLAVEOF NO ONE` ✓
2. reconf-slaves 단계: slaves 목록에 남은 hostname 정체성(`redis-master:6379`)에
   `REPLICAOF 172.24.0.2:6379` 전송
3. 그 주소의 실체는 방금 승격시킨 노드 → **자기 자신을 master 로 삼는 replica 로 강등**
4. 영원히 동기화되지 않는 read-only 상태 → 쓰기 전부 거절 → sentinel 이 재차 down 판정

Sentinel 의 hostname 지원(`resolve-hostnames`, 6.2+)은 opt-in 이고 공식 문서도 IP 를
기본으로 둔다. Docker Compose 에서 서비스명을 그대로 monitor 에 쓰는 편의가 sentinel 의
주소 기반 정체성 모델과 충돌한 사례다.

## 수정

sentinel 기동 시 서비스명을 IP 로 해석해 monitor 에 박는다 — sentinel 에게는 처음부터
IP 단일 정체성만 보이게 한다 (docker-compose.yaml sentinel entrypoint):

```sh
MASTER_IP=$(getent hosts redis-master | awk '{print $1}')
sentinel monitor mymaster $MASTER_IP 6379 2
```

`resolve-hostnames`·`announce-hostnames` 는 제거.

## 검증

사고가 났던 노드만 승격 후보로 남기고(다른 replica `replica-priority 0`) master 를 kill
→ 해당 노드 승격 후 `REPLICAOF` 자기 강등 없음, 40초 이상 master 유지, 2차 페일오버·
odown 재발 없음.

## 부수 관측

자기 강등으로 새 master 가 ~30초 먹통이던 구간에도 **투표 성공률 100%** — DB `OPEN`
관문·서킷·DB COUNT 저하 경로가 sentinel 오동작이라는 예상 밖 장애까지 흡수했다
(RESULTS.md 발견 2의 극단 사례).
