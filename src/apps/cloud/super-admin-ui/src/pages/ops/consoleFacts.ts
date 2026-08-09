/* API 응답 → 규칙 사실, 그리고 그 응답의 조회 상태 (ALPHA-738).
 *
 * ⚠️ **`shared.tsx`(JSX)에 있으면 `node --test` 가 import 을 못 한다.** 두 라운드 연속으로
 * 결함이 정확히 여기 있었는데(뉴스 DEAD 를 벤더마다 복제 · 캐시가 남은 조회 실패를 "실림"으로)
 * 변이를 걸어도 전건 통과했다. 기준은 "화면 도메인을 아는가"가 아니라 **"JSX 를 쓰는가"** 다 —
 * 이 두 함수는 안 쓴다.
 */
import { datasetKind } from '../../domains/sources/minuteView.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import type { MinuteFacts } from '../../rules/types.ts';
import type { AxisFetch } from './notRun.ts';

/**
 * API DTO → 규칙 사실. **규칙은 화면 도메인을 모른다** — 맞추는 일은 여기서 한 번만 한다.
 *
 * 이 어댑터의 판단은 `deadJobs` 를 **어디서** 가져오는가, 그리고 그 값이 **어느 축인가** 둘이다.
 * 뉴스 job 은 세션 연결 컬럼이 없어 날짜 축 집계(`newsJobs`)이고 가격은 세션에 붙어 있다.
 * 규칙은 그 사정을 모른 채 "이 데이터셋의 DEAD 수 + 그게 세션 축인가"만 받는다 —
 * 축을 안 밝히면 규칙이 날짜 집계를 벤더마다 복제해 같은 사실을 여러 사건으로 낸다.
 */
export function minuteFacts(s: MinuteStatus): MinuteFacts {
  /* 날짜 축 집계인 데이터셋을 **한 곳에** 모은다 — 세션마다 실으면 같은 데이터셋의 두 세션이
   * 서로 다른 축을 갖는 상태가 표현 가능해지고, 그게 안 난다는 보증이 이 map 의 습관에 걸린다.
   * 데이터셋 집합으로 올리면 그 상태가 아예 표현 불가다. */
  const byDateAxis = new Set<string>();
  const sessions = s.sessions.map((x) => {
    const kind = datasetKind(x.dataset);
    const news = kind === 'news';
    if (news) byDateAxis.add(x.dataset);
    return {
      dataset: x.dataset,
      /* 세션 identity 는 `(dataset, sourceGroup, date)` 다 — 여기서 버리면 규칙이 벤더가 다른
       * 두 세션을 한 대상으로 보고, 사건 식별자가 겹쳐 딥링크가 다른 세션을 연다 */
      sourceGroup: x.sourceGroup,
      phase: x.phase,
      leaseExpired: x.leaseExpired,
      overdueNoEvidence: x.windows.overdueNoEvidence,
      /* 어휘 밖 데이터셋(`other`)은 **어느 원장을 읽어야 할지 모른다** — `priceJobs` 로 접으면
       * 응답에 행이 없어 0이 되고, 화면이 그걸 "봤고 괜찮다"로 그린다. 세 번째 실시간
       * 데이터셋(`inav_minute`)이 붙는 날 정확히 그 모양이 된다. 모름은 `null` 로 낸다. */
      deadJobs: kind === 'other' ? null : (news ? s.newsJobs : x.priceJobs).dead,
    };
  });
  return { date: s.date, sessions, deadJobsByDate: [...byDateAxis] };
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
