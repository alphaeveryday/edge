/* API 응답 → 규칙 사실, 그리고 그 응답의 조회 상태 (ALPHA-738).
 *
 * ⚠️ **`shared.tsx`(JSX)에 있으면 `node --test` 가 import 을 못 한다.** 두 라운드 연속으로
 * 결함이 정확히 여기 있었는데(뉴스 DEAD 를 벤더마다 복제 · 캐시가 남은 조회 실패를 "실림"으로)
 * 변이를 걸어도 전건 통과했다. 기준은 "화면 도메인을 아는가"가 아니라 **"JSX 를 쓰는가"** 다 —
 * 이 두 함수는 안 쓴다.
 */
import { datasetKind, NEWS_MINUTE_DATASET } from '../../domains/sources/minuteView.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import type { ConsoleFactsDto } from '../../domains/console/types.ts';
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
 * 배치 사실 축의 조회 상태 — **응답 결함은 규칙별 `못 돎` 이 아니라 화면 단위 조회 실패다**
 * (계약 §「B 의 선행 조건」). 검증기가 거부한 응답은 축이 안 온 것과 다른 사실이라 `error` 로
 * 접는다: 축 부재는 규칙이 `canRun` 으로 답하고, 망가진 응답은 부분적으로도 못 믿는다.
 *
 * `stale` 은 `axisOf` 와 같은 뜻이다 — 직전 응답은 검증을 통과했는데 마지막 조회가 실패했다.
 * 여기서 `loaded` 로 접으면 1분마다 갱신되는 화면에서 낡은 판정이 현재 사실처럼 그려진다.
 */
export function factsAxis(parse: FactsParse | null, isError: boolean): AxisFetch {
  return axisOf(parse?.ok === true, isError || parse?.ok === false);
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

/* ────────────────────────────────────────────────────────────────────────────
 * 응답 검증 경계 (ALPHA-738 B2b · 계약 §「B 의 선행 조건」)
 *
 * ⛔ **규칙마다 값 가드를 다는 방식은 끝나지 않는다.** A2 리뷰에서 실증됐다 — `Number.isFinite`
 * 를 붙이면 다음 라운드가 `[]` 를, 그 다음이 `[null]` 을, 그 다음이 음수를 찾는다. 19규칙 ×
 * 값 종류만큼 늘고, 가드가 여럿이면 나중에 한쪽만 고쳐진다.
 *
 * 그래서 **규칙 층에 넘기기 전에 한 번** 본다. 규칙은 자기 타입을 믿을 수 있어야 하고, 그래야
 * `canRun`·`run()`·`note` 가 값 방어가 아니라 **판정**만 말한다.
 *
 * 🔴 **거부는 규칙별 `못 돎` 이 아니라 화면 단위 조회 실패다** — 축이 안 온 것(계측 공백)과
 * 응답이 망가진 것(계약 위반)은 다른 사실이다. 호출자는 이 실패를 `AxisFetch: 'error'` 로
 * 옮긴다. 일부 행만 버리지 않는 이유도 같다: 망가진 응답은 부분적으로도 못 믿는다.
 * ──────────────────────────────────────────────────────────────────────────── */

export type FactsParse = { ok: true; facts: Facts } | { ok: false; reason: string };

/* 필드 검사기 — 한 자리에 모아 두고 축별 표가 이걸 가리킨다.
 *
 * 🔴 **정수 카운트와 비율을 가른다.** 와이어에서 건수는 `long` 이라 소수가 올 수 없는데
 * `Number.isFinite` 만 보면 `0.5 건` 이 통과한다. 기준(중앙값)만 `Double` 이라 소수가 정상이다
 * (짝수 표본의 평균) — 둘을 한 검사로 묶으면 한쪽이 반드시 틀린다. */
type Check = (v: unknown) => boolean;
const text: Check = (v) => typeof v === 'string';
const nullableText: Check = (v) => v === null || text(v);
/**
 * 날짜·시각 — **문자열인지만 보면 부족하다.**
 *
 * 이 값들은 곧장 포매터로 간다: `kst()` 는 `new Date(iso)` 를 `Intl` 에 넘기고, 값이 파싱되지
 * 않으면 `RangeError: Invalid time value` 로 **렌더가 죽는다**. 그러면 응답 결함이 화면 단위
 * 조회 실패가 아니라 정체불명의 붕괴로 나온다 — 이 경계가 존재하는 이유의 정반대다.
 * 날짜 축이 조용히 틀리는 쪽도 있다: `tradingLag` 는 두 문자열을 사전순 비교해 형식이 깨지면
 * **지연 0** 을 실측처럼 낸다.
 *
 * ⛔ **`Date.parse` 를 게이트로 쓰지 않는다.** 두 방향으로 다 틀린다(실측):
 *   · 너무 관대 — `'2026'`·`'Aug 3 2026'` 을 받고, 특히 `'2026-02-30T12:00Z'` 를 **3월 2일로
 *     굴려** 통과시킨다. 없는 날이 조용히 **다른 실재 날**이 되어 화면에 실측처럼 선다.
 *   · 너무 엄격 — Java 가 낼 수 있는 초 단위 오프셋(`+09:00:30`)에 `NaN` 을 준다. 검증기가
 *     과하면 정상 응답을 통째로 버리고, 그날의 사고가 화면에서 사라진다.
 *
 * 그래서 **서버 문법을 전사하되, 소비자가 읽을 수 있는 범위까지만** 받는다 — 그 둘은 같지
 * 않다. 통과시킨 값은 곧장 `new Date()` → `Intl` 로 가므로, **JS 가 못 읽는 문자열을 받는 것은
 * 거부하는 것보다 나쁘다**: 거부는 사유가 붙은 화면 단위 조회 실패지만, 통과는 `RangeError` 로
 * 렌더가 죽는 정체불명의 붕괴다.
 *
 * ⚠️ 그래서 **초 단위 오프셋(`+09:00:30`)은 받지 않는다.** Java `OffsetDateTime` 이 이론상 낼 수
 * 있지만 `new Date()` 가 `Invalid Date` 를 주고(실측) `kst()` 가 그 자리에서 던진다. 이 원장의
 * 시각은 Postgres `timestamptz` 를 KST 세션으로 읽은 값이라 오프셋이 항상 `+09:00`·`Z` 다 —
 * 도달 경로가 없는 형태를 받아 주려다 렌더를 죽이는 쪽을 택할 이유가 없다.
 * ⚠️ 확장 연도(`+010000-01-01`·음수 연도)도 같은 이유로 안 받는다.
 */
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
/** 오프셋은 `Z` 또는 `±HH:MM` 만 — 시 ≤ 18, 분 < 60 은 아래에서 따로 본다(정규식은 못 센다). */
const INSTANT_RE =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(?:Z|([+-])(\d{2}):(\d{2}))$/;

/**
 * 달력에 실재하는 날인가 — `2026-02-30` 을 걸러내는 유일한 검사다(정규식은 못 본다).
 *
 * ⚠️ `Date.UTC(y, …)` 를 쓰면 안 된다 — **연도 0~99 를 1900+ 로 보정**해서 `0050-01-01` 이
 * 1950년이 되고, 문법상 멀쩡한 값이 조용히 거부된다(실측). `setUTCFullYear` 는 그 보정이 없다.
 */
const realDate = (y: number, m: number, d: number): boolean => {
  const t = new Date(0);
  t.setUTCFullYear(y, m - 1, d);
  t.setUTCHours(0, 0, 0, 0);
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
};

const isoDate: Check = (v) => {
  if (!text(v)) return false;
  const m = DATE_RE.exec(v as string);
  return m !== null && realDate(+m[1], +m[2], +m[3]);
};
const isoInstant: Check = (v) => {
  if (!text(v)) return false;
  const m = INSTANT_RE.exec(v as string);
  if (m === null || !realDate(+m[1], +m[2], +m[3])) return false;
  /* 24:00 은 ISO 가 허용하지만 `OffsetDateTime` 은 안 낸다 — 굳이 받아 시각 축을 흐리지 않는다 */
  if (!(+m[4] < 24 && +m[5] < 60 && (m[6] === undefined || +m[6] < 60))) return false;
  /* 🔴 오프셋도 **범위를 센다.** 정규식은 자릿수만 보므로 `+99:99` 가 통과하는데, 그 값은
   * `new Date()` 가 `Invalid Date` 를 주고 `kst()` 가 던진다 — 이 경계가 없애려던 붕괴 그대로다.
   * `m[7]` 이 없으면 `Z` 다(오프셋 캡처가 안 잡힌 경우 — `null` 이 아니라 `undefined` 다).
   * ⚠️ 상한은 `<= 18` 이 아니라 **정확히 ±18:00** 이다. `<= 18` 로 두면 `+18:59` 가 통과하는데
   * `ZoneOffset` 은 그 값을 만들지 못한다 — 계약 밖 값을 받아 주는 쪽으로 새던 자리다. */
  if (m[7] === undefined) return true;
  const oh = +m[8];
  const om = +m[9];
  return om < 60 && (oh < 18 ? true : oh === 18 && om === 0);
};
const nullableDate: Check = (v) => v === null || isoDate(v);
const nullableInstant: Check = (v) => v === null || isoInstant(v);
const bool: Check = (v) => typeof v === 'boolean';
/** 서버가 그 슬롯에만 싣는 필드 — 없는 것이 정상이다(필드 단위 `NON_NULL`). */
const optionalBool: Check = (v) => v === undefined || bool(v);
/**
 * 건수 — **안전 정수**이고 음수가 아니다.
 *
 * `isInteger` 가 아니라 `isSafeInteger` 인 이유: 와이어의 건수는 `long` 이라 2^53 을 넘을 수 있고,
 * 그때 `JSON.parse` 는 **이미 반올림한 값**을 준다. `isInteger` 는 그 손상된 값을 통과시키므로
 * `expected` 와 `received` 가 1 차이인 응답이 같은 수가 되어 **R07 이 결손을 정상으로 판정**한다
 * (리뷰가 잡았다). 도달 확률은 낮지만 검사 비용이 같아서 옳은 쪽을 쓴다.
 */
const count: Check = (v) => Number.isSafeInteger(v) && (v as number) >= 0;
const nullableCount: Check = (v) => v === null || count(v);
/** 기준값 — 짝수 표본의 중앙값이라 소수가 정상이다. */
const ratio: Check = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const nullableRatio: Check = (v) => v === null || ratio(v);

/* 축별 **전수** 검사표. 부분만 검사하고 캐스트하면 나머지 필드는 무검증으로 규칙에 흘러가고,
 * 그러면 이 경계가 약속한 "규칙은 자기 타입을 믿어도 된다"가 거짓이 된다(리뷰가 잡았다 —
 * `planned: "false"` 는 truthy 라 R01 이 거짓 P0 를 낸다).
 *
 * ⚠️ **모르는 필드는 거부하지 않는다.** 서버가 축을 하나 더 실었다고 콘솔이 죽으면, 전진하는
 * 배포가 화면을 멈춘다. 표는 "이 필드들이 이 타입이어야 한다"만 말한다.
 * 표가 낡는 것은 `consoleFacts.test.ts` 의 집합 불변식이 막는다(픽스처의 키 ⊆ 표의 키). */
export const RUN_FIELDS: Record<string, Check> = {
  id: text, lane: nullableText, tradingDate: nullableDate, ledgerStatus: nullableText,
  ledgerUpdated: nullableInstant, deadline: nullableInstant,
  planned: optionalBool, noRunRow: optionalBool,
};
export const TASK_FIELDS: Record<string, Check> = {
  taskKey: text, runId: text, pipelineType: text, tradingDate: nullableDate, stage: text,
  dataset: nullableText, required: bool, planStatus: text, taskOutcome: nullableText,
  dataStatus: nullableText, recordsOut: nullableCount, failedRecords: nullableCount,
  completenessExpected: nullableCount, completenessReceived: nullableCount,
  completenessMissing: nullableCount, attempts: count,
};
export const DATASET_FIELDS: Record<string, Check> = {
  id: text, contract: bool, expectedAsOf: nullableDate, actualAsOf: nullableDate,
  collectedAt: nullableInstant, unverifiable: nullableText,
};
export const OUTPUT_FIELDS: Record<string, Check> = {
  id: text, label: text, unit: text, today: count, base: nullableRatio,
};
export const BOUNDARY_FIELDS: Record<string, Check> = {
  publishedWithoutDelivery: count, deliveryNowNonpublished: count, deliveryRows: count,
};
/* 🔴 **체인의 수에는 `null` 자리가 없다.** 서버가 코호트를 정해 놓고 세므로 "못 셌다"가 없고,
 * 0 은 그 단계에서 사라졌다는 실측이다(R10 의 P0). `nullableCount` 를 쓰면 손상된 응답의 `null`
 * 이 규칙 층까지 흘러가 그 단계만 비교에서 조용히 빠지고, 손실이 "여기는 안 셌구나"로 접힌다. */
export const CHAIN_FEED_FIELDS: Record<string, Check> = {
  id: text, label: text, v: count, unit: text, src: text,
};
export const CHAIN_STAGE_FIELDS: Record<string, Check> = {
  id: text, label: text, batch: count, intraday: count, src: text,
};
export const META_FIELDS: Record<string, Check> = { db: isoInstant, today: isoDate };

/** 배열 원소는 **객체**여야 한다 — `[null]`·`[1]`·`[[]]` 가 여기서 걸린다. */
const isRow = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** 어긋난 첫 필드 이름을 돌려준다(없으면 null) — 사유가 어느 자리인지 말해야 원인을 가리킨다. */
function offendingField(row: Record<string, unknown>, fields: Record<string, Check>): string | null {
  for (const [name, ok] of Object.entries(fields)) {
    if (!ok(row[name])) return name;
  }
  return null;
}

/**
 * 와이어 응답 → 규칙 사실. 형상이 틀리면 **거부한다**(사유와 함께).
 *
 * 검증 범위는 계약이 "검증 경계가 답할 몫"으로 열거한 것이다: 컬렉션 원소가 객체가 아닌 경우 ·
 * 음수·비정수 카운트 · 수여야 할 자리가 수가 아닌 경우 · 문자열·불리언 자리의 타입 · 필수 축의
 * 종류. **서버가 안 보내는 축(`queues`·`runbook`·`meta.aws`)은 검사하지 않는다** — 그건 계측
 * 공백이지 응답 결함이 아니고, 규칙이 `canRun` 으로 답할 몫이다.
 *
 * 🔴 **과하면 정상 응답을 통째로 버린다.** 서버가 정당하게 내는 `null`(비거래일 런의 거래일 ·
 * 슬롯 키를 못 읽은 레인 · 기준 없는 산출 · 미배선 완전성)을 거부하면 그날의 사고가 화면에서
 * 사라진다 — 검증기가 만드는 거짓 안심이다.
 */
export function parseFacts(body: unknown): FactsParse {
  const bad = (reason: string): FactsParse => ({ ok: false, reason });
  if (!isRow(body)) return bad('응답이 객체가 아니다');

  const AXES = [
    ['runs', RUN_FIELDS], ['tasks', TASK_FIELDS],
    ['datasets', DATASET_FIELDS], ['outputs', OUTPUT_FIELDS],
  ] as const;
  for (const [axis, fields] of AXES) {
    const rows = body[axis];
    if (!Array.isArray(rows)) return bad(`${axis} 축이 배열이 아니다`);
    for (const row of rows) {
      if (!isRow(row)) return bad(`${axis} 원소가 객체가 아니다`);
      const field = offendingField(row, fields);
      if (field) return bad(`${axis}[].${field} 의 값이 계약과 다르다`);
    }
  }
  for (const [axis, fields] of [['boundary', BOUNDARY_FIELDS], ['meta', META_FIELDS]] as const) {
    const row = body[axis];
    if (!isRow(row)) return bad(`${axis} 축이 객체가 아니다`);
    const field = offendingField(row, fields);
    if (field) return bad(`${axis}.${field} 의 값이 계약과 다르다`);
  }

  /* 체인만 형상이 둘이다 — 객체 하나 안에 배열이 둘이다. 그래서 위 두 루프 어느 쪽에도 안
   * 들어간다. **빈 배열도 거부한다**: 소비자는 `feeds[0]`·`feeds[1]` 을 **위치로** 읽으므로
   * (id 로 찾지 않는다) 갈래가 없는 응답은 그 자리에서 `undefined` 가 되고, 그러면 그 갈래의
   * 첫 비교점이 사라져 손실 하나가 통째로 안 보인다. 계약상 갈래는 늘 둘이다. */
  const chain = body.chain;
  if (!isRow(chain)) return bad('chain 축이 객체가 아니다');
  for (const [part, fields] of [['feeds', CHAIN_FEED_FIELDS],
    ['stages', CHAIN_STAGE_FIELDS]] as const) {
    const rows = chain[part];
    if (!Array.isArray(rows) || rows.length === 0) return bad(`chain.${part} 가 비었다`);
    for (const row of rows) {
      if (!isRow(row)) return bad(`chain.${part} 원소가 객체가 아니다`);
      const field = offendingField(row, fields);
      if (field) return bad(`chain.${part}[].${field} 의 값이 계약과 다르다`);
    }
  }
  if ((chain.feeds as unknown[]).length !== 2) return bad('chain.feeds 가 두 갈래가 아니다');

  return { ok: true, facts: toFacts(body as unknown as ConsoleFactsDto) };
}

/**
 * 검증된 와이어 → 엔진 사실. **이름만 바꾼다** — 값을 메우거나 접지 않는다.
 *
 * 서버가 안 보낸 축은 여기서도 안 만든다(`queues`·`runbook`·`meta.aws`). 빈 값으로 채우면
 * 계측 없음이 실측으로 위조되고, 규칙이 `못 돎` 대신 `평가됨 · 위반 0` 을 세운다.
 */
function toFacts(dto: ConsoleFactsDto): Facts {
  return {
    runs: dto.runs.map((r) => ({
      id: r.id,
      lane: r.lane,
      trading_date: r.tradingDate,
      ledger_status: r.ledgerStatus,
      ledger_updated: r.ledgerUpdated,
      deadline: r.deadline,
      ...(r.planned !== undefined ? { planned: r.planned } : {}),
      ...(r.noRunRow !== undefined ? { no_run_row: r.noRunRow } : {}),
    })),
    tasks: dto.tasks.map((t) => ({
      task_key: t.taskKey,
      run_id: t.runId,
      pipeline_type: t.pipelineType,
      trading_date: t.tradingDate,
      stage: t.stage,
      dataset: t.dataset,
      required: t.required,
      plan_status: t.planStatus,
      task_outcome: t.taskOutcome,
      data_status: t.dataStatus,
      records_out: t.recordsOut,
      failed_records: t.failedRecords,
      completeness_expected: t.completenessExpected,
      completeness_received: t.completenessReceived,
      completeness_missing: t.completenessMissing,
      attempts: t.attempts,
    })),
    datasets: dto.datasets.map((d) => ({
      id: d.id,
      contract: d.contract,
      expected_as_of: d.expectedAsOf,
      actual_as_of: d.actualAsOf,
      collected_at: d.collectedAt,
      unverifiable: d.unverifiable,
    })),
    outputs: dto.outputs.map((o) => ({
      id: o.id,
      label: o.label,
      today: o.today,
      base: o.base,
      unit: o.unit,
    })),
    boundary: {
      published_without_delivery: dto.boundary.publishedWithoutDelivery,
      delivery_now_nonpublished: dto.boundary.deliveryNowNonpublished,
      delivery_rows: dto.boundary.deliveryRows,
    },
    /* 순서를 **그대로 옮긴다** — 목록 순서가 곧 흐름이라 정렬하거나 id 로 다시 찾으면 서버가
     * 정한 선후가 사라진다. 원장에는 단계 간 선후가 없어 여기서 복원할 방법도 없다. */
    chain: {
      feeds: dto.chain.feeds.map((f) => ({
        id: f.id,
        label: f.label,
        v: f.v,
        unit: f.unit,
        src: f.src,
      })),
      stages: dto.chain.stages.map((s) => ({
        id: s.id,
        label: s.label,
        batch: s.batch,
        intraday: s.intraday,
        src: s.src,
      })),
    },
    meta: { db: dto.meta.db, today: dto.meta.today },
  };
}
