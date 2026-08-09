/* API 응답 → 규칙 사실, 그리고 그 응답의 조회 상태 (ALPHA-738).
 *
 * ⚠️ **`shared.tsx`(JSX)에 있으면 `node --test` 가 import 을 못 한다.** 두 라운드 연속으로
 * 결함이 정확히 여기 있었는데(뉴스 DEAD 를 벤더마다 복제 · 캐시가 남은 조회 실패를 "실림"으로)
 * 변이를 걸어도 전건 통과했다. 기준은 "화면 도메인을 아는가"가 아니라 **"JSX 를 쓰는가"** 다 —
 * 이 두 함수는 안 쓴다.
 */
import { datasetKind, NEWS_MINUTE_DATASET } from '../../domains/sources/minuteView.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import type { Facts, MinuteFacts } from '../../rules/types.ts';
import type { AxisFetch } from './notRun.ts';

/**
 * API DTO → 규칙 사실. **규칙은 화면 도메인을 모른다** — 맞추는 일은 여기서 한 번만 한다.
 *
 * 이 어댑터의 판단은 `deadJobs` 를 **어디에** 싣는가 하나다. 가격 job 은 세션에 붙어 있고
 * (`session_id`), 뉴스 job 은 `created_at` 하루 창 집계라 세션과 다른 컬럼으로 잘린다.
 * 그래서 값이 **두 자리**로 나간다 — 세션의 `deadJobs`(세션 축)와 `deadJobsByDataset`(날짜 축).
 * 규칙은 그 사정을 모른 채 어느 자리에 있는지만 보고 사건의 입도를 정한다.
 */
export function minuteFacts(s: MinuteStatus): MinuteFacts {
  return {
    date: s.date,
    sessions: s.sessions.map((x) => ({
      dataset: x.dataset,
      /* 세션 identity 는 `(dataset, sourceGroup, date)` 다 — 여기서 버리면 규칙이 벤더가 다른
       * 두 세션을 한 대상으로 보고, 사건 식별자가 겹쳐 딥링크가 다른 세션을 연다.
       * ⚠️ 빈 값을 **메우지 않는다**: 메우면 하류의 조각 가드가 영영 안 뜨는 죽은 분기가 된다. */
      sourceGroup: x.sourceGroup,
      phase: x.phase,
      leaseExpired: x.leaseExpired,
      overdueNoEvidence: x.windows.overdueNoEvidence,
      /* 세션에 붙은 job 원장은 **가격뿐**이다. 뉴스는 아래 날짜 축으로 나가고, 어휘 밖
       * 데이터셋(`other`)은 어느 원장을 읽어야 할지 모른다 — 0으로 접으면 원장 부재가
       * "봤고 괜찮다"로 그려진다. 모름은 `null` 이다. */
      deadJobs: datasetKind(x.dataset) === 'price' ? x.priceJobs.dead : null,
    })),
    /* 🔴 **세션을 순회해서 만들지 않는다.** `newsJobs` 는 `news_extraction_job` 을 `created_at`
     * 하루 창으로 센 값이고, 그 표에는 `session_id` 도 `session_date` 도 없다 — 세션과 **다른
     * 컬럼으로 잘리는 축**이다. 세션 순회로 읽으면 그날 뉴스 세션이 없을 때(아침 planner 전 ·
     * 비거래일 · **뉴스 계획만 실패한 날**) DEAD 가 통째로 사라지고 R19 가 "위반 0"을 낸다 —
     * 하필 가장 시끄러워야 할 날에. `MinutePage` 도 같은 이유로 뉴스 데이터셋을 세션과 무관하게
     * 세운다(`DATASET_ORDER` 합집합). 키가 곧 축 선언이라 표기를 따로 두지 않는다. */
    deadJobsByDataset: { [NEWS_MINUTE_DATASET]: s.newsJobs.dead },
  };
}

/**
 * 실시간 축 응답의 상태 — **데이터 유무와 조회 성공은 다른 축**이다.
 *
 * react-query 는 에러가 나도 **직전 데이터를 남긴다**(`status:'error'` + `data` 유지). 그리고 이
 * 화면의 쿼리는 `refetchInterval: 60_000` 이라, 운영자가 띄워 둔 화면에서 5xx 가 나는 지배적
 * 경로가 정확히 그 조합이다. `data != null` 만 보고 "실림"이라고 쓰면 **낡은 판정을 현재
 * 사실처럼** 그린다 — 그래서 `stale` 을 따로 낸다.
 */
export function axisOf(hasData: boolean, isError: boolean): AxisFetch {
  if (!hasData) return isError ? 'error' : 'pending';
  return isError ? 'stale' : 'loaded';
}

/**
 * AWS 제어면 관측 시각의 **부재 종류** — 값이 아니라 종류를 답한다.
 *
 * 부재가 두 형상이고 뜻이 다르다(계약 §부재를 싣는 규약): 키가 없으면 **미배선**(조회를 시도조차
 * 안 했다), 키가 있고 `null` 이면 **조회했는데 못 봤다**(예: `AccessDenied` — 서버가
 * `awsUnavailable` 사유를 함께 보낸다). `kst(undefined)` 처럼 포매터에 그냥 넘기면 둘 다
 * `—`(집계 없음)로 접혀, 제어면 장애가 "계측이 없구나"로 읽힌다.
 *
 * ⚠️ **`in` 이 아니라 값으로 가른다** — 어댑터가 누락 필드를 `aws: undefined` 로 명시하면
 * (객체 spread·직접 대입) `'aws' in meta` 는 참이라 미배선이 조회 실패로 뒤집힌다.
 *
 * ⚠️ 이 판단이 `shared.tsx`(JSX) 안에 있던 동안은 `node --test` 가 import 을 못 해 **변이가
 * 하나도 안 잡혔다** — `notRun`·`minuteFacts` 를 여기로 내린 것과 같은 이유다.
 */
export function awsObservation(meta: Facts['meta']): 'uninstrumented' | 'blind' | { at: string } {
  if (meta.aws === undefined) return 'uninstrumented';
  return meta.aws === null ? 'blind' : { at: meta.aws };
}
