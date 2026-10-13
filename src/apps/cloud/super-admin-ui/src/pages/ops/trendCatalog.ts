/* 추이 지표 카탈로그 — facts 를 지표로 옮긴다 (ALPHA-738).
 *
 * 여기서만 사실을 읽는다(trendMetrics 는 순수 계산). 일별 API가 없는 지표는 조회일 값 한 점만
 * 내고 과거를 만들지 않는다. 엔티티 해소율은 별도 API의 실측 계열을 쓴다. 값을 응답에서 읽지
 * 못하는 지표는 목록에 두지 않는다.
 *
 * 지표를 고른 기준(요구 §3):
 *   · 일배치는 데이터셋마다 하나씩 식별되게 — 트리거 하나가 일배치 전체를 대표하지 않는다.
 *   · 장중은 분 단위 원시 개수가 아니라 **비율·지연·결손 수**로 집계한다.
 *   · 뉴스는 문서·assertion·source event 절대 수를 동급으로 나열하지 않고 2~3개로 줄인다.
 *     단계별 감소는 데이터 화면의 뉴스 처리 퍼널이 답한다(여기서 반복하지 않는다).
 *   · 분석 결과는 **단위가 같은 것끼리만** 비율을 낸다. 트리거 이벤트 대비 성공률은
 *     중복 제거된 분석 대상 계측이 없어 계산하지 않고 `계측 없음` 으로 둔다.
 *   · Cloud 게시·테넌트 발번·소비자 전달은 전달 화면 소관이라 지표로 두지 않는다.
 */
import type { Facts } from '../../rules/types.ts';
import type { EntityResolutionTrendDto } from '../../domains/console/types.ts';
import type { MinuteStatus } from '../../domains/sources/types.ts';
import type { Metric, SeriesPoint } from './trendMetrics.ts';

/** 기준일 지연 — 거래소 휴장 캘린더 축이 없어 (actual, expected] 사이의 평일 수만 센다. */
export function tradingLag(actualISO: string | null, expectedISO: string | null): number | null {
  if (!actualISO || !expectedISO) return null;
  if (actualISO >= expectedISO) return 0;
  let n = 0;
  const d = new Date(`${actualISO}T00:00:00Z`);
  const end = new Date(`${expectedISO}T00:00:00Z`);
  while (d < end) {
    d.setUTCDate(d.getUTCDate() + 1);
    const day = d.getUTCDay();
    if (day !== 0 && day !== 6) n += 1;
  }
  return n;
}

/** 같은 작업이 여러 슬롯에 있으면 run_key의 시각 규약상 가장 늦은 슬롯을 오늘 값으로 쓴다. */
export const latestTask = (f: Facts, key: string) =>
  f.tasks
    .filter((t) => t.task_key === key)
    .sort((a, b) => b.run_id.localeCompare(a.run_id))[0];
const dataset = (f: Facts, id: string) => f.datasets.find((d) => d.id === id);
/* 축이 통째로 없으면 `null` — 부재를 0 으로 그리면 "결과 생성률 0%" 라는 거짓 경보가 된다
 * (`coverageMetric`·`lagMetric` 의 `comparisonType: 'uninstrumented'` 규약과 같은 방향). */
const chain = (f: Facts, id: string) => f.chain?.stages.find((s) => s.id === id)?.batch ?? null;
const output = (f: Facts, id: string) => f.outputs.find((o) => o.id === id);

/**
 * 완전성 = received / expected. 분모가 없거나 **0 이면** null(0 이 아니라 판정 불가).
 *
 * 🔴 `expected === 0` 을 안 막으면 `0/0 = NaN` 이 되고, `NaN < 0.95` 가 **false** 라 카드가
 * `기준 충족`(초록) · `NaN%` 로 선다 — 검증 경계는 음수만 막지 0 은 정당한 건수로 통과시킨다.
 * `hasBase`(R13 의 `base: 0`)와 같은 판단이다: 나눗셈이 성립하지 않는 분모는 기준이 아니다.
 */
function coverage(f: Facts, taskKey: string): { value: number | null; mock: boolean } {
  const t = latestTask(f, taskKey);
  const expected = t?.completeness_expected;
  if (!t || expected == null || expected <= 0 || t.completeness_received == null) {
    return { value: null, mock: false };
  }
  return { value: t.completeness_received / expected, mock: t.cmpl_mock === true };
}

const dsDrill = (id: string) => ({ href: `/ops/datasets?focus=ds-${id}`, label: '데이터셋 상세' });

/**
 * 계측이 없어 판정하지 않는 지표 — **부재를 0 으로 그리지 않는다.**
 *
 * 축이 통째로 없을 때 `today: 0` 으로 계열을 만들면 "결과 생성률 0%" 라는 거짓 경보가 뜬다.
 * 응답에 축이 없는 것은 실측 0 이 아니라 **모른다**이고, 그때 카드는 정상으로도 실패로도
 * 서면 안 된다(`evaluateMetric` 의 `uninstrumented` 갈래).
 */
function uninstrumented(m: Omit<Metric, 'comparisonType' | 'threshold' | 'series'>): Metric {
  return { ...m, comparisonType: 'uninstrumented', threshold: null, series: [] };
}

const OUTPUT_ABSENT = (id: string) =>
  [
    `산출 축 \`${id}\` 가 이번 응답에 없다 — 셀 값 자체가 없다.`,
    '',
    '0 으로 그리면 "오늘 하나도 안 나왔다"가 되는데, 그건 이 응답이 답하지 못하는 말이다.',
  ].join('\n');

const CHAIN_ABSENT = [
  '체인 단계 집계(`chain`)가 이번 응답에 없다 — 그 축을 안 싣는 배포본을 보고 있다(계약 §체인 축).',
  '',
  '부재를 0 으로 접으면 "결과 생성률 0%" 라는 거짓 경보가 뜬다.',
].join('\n');

/**
 * 조회일 값 한 점 — **실측 과거가 없으면 과거를 만들지 않는다.**
 *
 * 🔴 오늘 값 둘레로 과거를 만들면 중앙값이 오늘 값과 같아져 편차 0% → **`정상 범위`** 가 선다:
 * 서버가 "평소를 모른다"(`base: null`)고 말한 날에 화면이 "평소와 같다"고 답하는 것이다.
 * 한 점만 두면 기준은 응답이 준 값(`Metric.baseline`)뿐이고, 그것도 없으면 `evaluateMetric` 의
 * `기준 없음` 갈래가 답한다: 오늘 값은 그리고 판정만 하지 않는다.
 */
const onePoint = (date: string, value: number, isMock = false): SeriesPoint[] => [{ date, value, isMock }];

function nextDay(date: string): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + 1);
  return value.toISOString().slice(0, 10);
}

/** 관측이 끊긴 구간을 선으로 잇지 않도록 최신 연속 일별 구간만 그린다. */
function latestContinuousRates(points: EntityResolutionTrendDto['points']): SeriesPoint[] {
  const series: SeriesPoint[] = [];
  let followingDate: string | null = null;
  for (let i = points.length - 1; i >= 0; i -= 1) {
    const point = points[i];
    if (point.rate === null || (followingDate !== null && nextDay(point.date) !== followingDate)) break;
    series.unshift({ date: point.date, value: point.rate, isMock: false });
    followingDate = point.date;
  }
  return series;
}

/** 실제 API 점만 쓰며, 최신 0/0은 0%가 아니라 판정 불가다. */
export function entityResolutionMetric(trend?: EntityResolutionTrendDto): Metric {
  const points = trend?.points ?? [];
  const latest = points.at(-1);
  const series = latestContinuousRates(points);
  const base = {
    id: 'n.entity_resolution_rate',
    label: '뉴스 엔티티 해소율',
    group: 'news' as const,
    unit: '비율',
    metricType: 'rate' as const,
    direction: 'higherIsBetter' as const,
    source: 'DB_LEDGER' as const,
    help: [
      '엔티티 해소율 = 하나 이상의 엔티티에 연결된 argument / 전체 argument.',
      latest
        ? `최신 관측 ${latest.resolvedArguments.toLocaleString('ko-KR')} / ${latest.totalArguments.toLocaleString('ko-KR')}.`
        : '아직 저장된 관측이 없다.',
      '',
      '판정: 최신 비율 60% 미만이면 주의(주황)다. 60% 이상은 기준 충족이다.',
      '0/0은 비율이 아니므로 계측 없음이며 정상·이상 어느 쪽에도 세지 않는다.',
      series.length < points.length && latest?.rate !== null
        ? '관측 공백은 선으로 잇지 않고 최신 연속 일별 구간만 그린다.'
        : '',
      '',
      '계열은 API가 반환한 원장 실측만 사용한다. 과거 값을 만들거나 미해소 원인을 추정하지 않는다.',
    ].join('\n'),
    drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
  };
  if (!latest || latest.rate === null) return uninstrumented(base);
  return {
    ...base,
    comparisonType: 'minRatio',
    threshold: 0.6,
    abnormalTone: 'warn',
    series,
  };
}

/* ── 일배치 ── 데이터셋마다 하나씩 */

function coverageMetric(
  f: Facts,
  id: string,
  label: string,
  taskKey: string,
  datasetId: string,
  denomLabel: string,
): Metric {
  const c = coverage(f, taskKey);
  const t = latestTask(f, taskKey);
  const TODAY = f.meta.today;
  return {
    id,
    label,
    group: 'batch',
    unit: '비율',
    metricType: 'coverage',
    comparisonType: c.value === null ? 'uninstrumented' : 'minRatio',
    threshold: c.value === null ? null : 0.95,
    direction: 'higherIsBetter',
    source: c.mock ? 'MOCK' : 'DB_LEDGER',
    series: c.value === null ? [] : onePoint(TODAY, c.value, c.mock),
    help: [
      `완전성 = 적재 ${denomLabel} / 기대 ${denomLabel} (원장 completeness 축).`,
      t?.completeness_expected == null
        ? '분모가 배선되지 않아 계산할 수 없다.'
        : t.completeness_expected === 0
          ? `오늘 기대 ${denomLabel}가 0이라 비율이 정의되지 않는다 — 미달로도 충족으로도 세지 않는다.`
          : `오늘 ${t.completeness_received}/${t.completeness_expected} ${denomLabel}.`,
      '',
      '판정: 계약 최소 95% 미만이면 기준 미달이다 — 산출량 지표의 ±25% 자를 쓰지 않는다.',
      '레코드 수가 아니라 엔티티 커버리지다(행 수는 하루 거래량에 따라 흔들린다).',
      '',
      '일별 계열을 주는 응답이 없어 오늘 값 한 점만 그린다.',
    ].join('\n'),
    drill: dsDrill(datasetId),
  };
}

function lagMetric(f: Facts, id: string, label: string, datasetId: string): Metric {
  const d = dataset(f, datasetId);
  const lag = tradingLag(d?.actual_as_of ?? null, d?.expected_as_of ?? null);
  const TODAY = f.meta.today;
  return {
    id,
    label,
    group: 'batch',
    unit: '거래일',
    metricType: 'lag',
    comparisonType: lag === null ? 'uninstrumented' : 'maxLagDays',
    threshold: lag === null ? null : 0,
    direction: 'lowerIsBetter',
    source: d?.mock ? 'MOCK' : 'DB_LEDGER',
    series: lag === null ? [] : onePoint(TODAY, lag),
    help: [
      '기준일 지연 = 기대 기준일과 실제 기준일 사이의 주말 제외 평일 수.',
      '거래소 휴장 캘린더 축은 이 응답에 없어 공휴일·임시 휴장일은 제외하지 못한다.',
      d?.expected_as_of ? `기대 ${d.expected_as_of} · 실제 ${d.actual_as_of ?? '없음'}.` : '',
      '',
      '판정: 허용 지연 0거래일 — 하루라도 밀리면 분석이 오래된 스냅샷 위에서 돈다.',
      '수집 시각(collected_at)과는 다른 축이다 — 오늘 수집해도 담긴 기준일은 어제일 수 있다.',
      '',
      '일별 계열을 주는 응답이 없어 오늘 값 한 점만 그린다.',
    ]
      .filter(Boolean)
      .join('\n'),
    drill: dsDrill(datasetId),
  };
}

/* ── 지표 목록 ── */

/**
 * 사실 → 지표. **모듈 최상위 상수가 아니라 함수다**(ALPHA-738 D).
 *
 * 🔴 예전에는 `export const METRICS` 가 import 시점에 평가되며 `output('o.doc')!.today` 를
 * 읽었다. 축이 비면 렌더가 아니라 **모듈 평가**에서 죽고, 그 자리는 `AdminLayout` 의
 * ErrorBoundary 밖이라 **흰 화면**이 된다. 사실을 인자로 받으면 그 `!` 들이 함께 없어진다 —
 * 부재는 죽을 자리가 아니라 `uninstrumented` 로 그릴 자리다.
 */
export function buildMetrics(f: Facts, minute?: MinuteStatus, entityResolution?: EntityResolutionTrendDto): Metric[] {
  const TODAY = f.meta.today;
  const minuteObserved = minute?.date === TODAY;
  const priceSessions = minuteObserved
    ? minute.sessions.filter((s) => s.dataset === 'price_minute')
    : [];
  const noEvidenceToday = minuteObserved
    ? priceSessions.reduce((sum, s) => sum + s.windows.overdueNoEvidence, 0)
    : undefined;
  const deadJobsToday = minuteObserved
    ? priceSessions.reduce((sum, s) => sum + s.priceJobs.dead, 0)
    : undefined;
  const doc = output(f, 'o.doc');
  const trig = output(f, 'o.trig');
  const res = chain(f, 'c.res');
  const run = chain(f, 'c.run');
  /* 분봉 응답이 없으면 값이 없다 — 숫자를 대신 넣지 않고 판정 불가로 둔다 */
  const minuteDefect = (m: Omit<Metric, 'comparisonType' | 'threshold' | 'series'>, today: number | undefined): Metric =>
    today === undefined
      ? uninstrumented(m)
      : { ...m, comparisonType: 'maxCount', threshold: 0, series: onePoint(TODAY, today) };
  const MINUTE_ABSENT = '조회일의 분봉 상태 응답이 없어 값을 그리지 않는다.';

  return [
  /* 일배치 */
  coverageMetric(f, 'b.price_daily', '가격 일봉 완전성', 'PRICE_COLLECTION_KIS', 'price_daily', '종목'),
  coverageMetric(f, 'b.etf_holdings', 'ETF 구성종목 완전성', 'ETF_HOLDINGS_COLLECTION_KRX', 'etf_holdings', 'ETF'),
  lagMetric(f, 'b.investor_flow', '수급 기준일 지연', 'investor_flow'),
  lagMetric(f, 'b.etf_nav', 'ETF NAV 기준일 지연', 'etf_nav'),

  /* 장중 — 분 단위 원시 개수가 아니라 비율·지연·결손 수로 */
  minuteDefect(
    {
      id: 'i.no_evidence',
      label: '1분 가격 · 무증거 창',
      group: 'intraday',
      unit: '창',
      metricType: 'defect',
      direction: 'lowerIsBetter',
      source: 'DB_LEDGER',
      help: [
        '무증거 창 = 수집 가능 시각(scheduled_at)이 지났는데 결과 증거가 없는 창(DUE 또는 유효 lease 없는 CLAIMED).',
        '',
        '판정: 1개 이상이면 이상 — 결손은 분포로 보지 않는다.',
        '이 수치만으로 미실행·실행체 사망을 확정하지 않는다.',
        '',
        noEvidenceToday === undefined
          ? MINUTE_ABSENT
          : '오늘 값은 분봉 원장 실측이다. 일별 계열을 주는 응답이 없어 한 점만 그린다.',
      ].join('\n'),
      drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
    },
    noEvidenceToday,
  ),
  minuteDefect(
    {
      id: 'i.dead_jobs',
      label: '1분 가격 · DEAD job',
      group: 'intraday',
      unit: '건',
      metricType: 'defect',
      direction: 'lowerIsBetter',
      source: 'DB_LEDGER',
      help: [
        '재시도가 소진된 처리 job 수(DB job 원장의 status=DEAD). 실제 큐 지표가 아니다.',
        '',
        '판정: 1건 이상이면 이상.',
        '이 응답에는 해소 축이 없어 당일 누적이다 — 이미 복구됐는지는 알 수 없다.',
        '',
        deadJobsToday === undefined
          ? MINUTE_ABSENT
          : '오늘 값은 분봉 원장 실측이다. 일별 계열을 주는 응답이 없어 한 점만 그린다.',
      ].join('\n'),
      drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
    },
    deadJobsToday,
  ),

  /* 뉴스 — 단계별 감소는 퍼널이 답한다 */
  doc
    ? {
        id: 'n.documents',
        label: '뉴스 문서 수',
        group: 'news',
        unit: '건',
        metricType: 'volume',
        comparisonType: 'medianDelta',
        threshold: 0.25,
        direction: 'stable',
        source: 'DB_LEDGER',
        /* 기준은 규칙(R13)이 쓰는 `base` 그대로다 — 두 화면이 다른 편차를 말하면 안 된다 */
        series: onePoint(TODAY, doc.today),
        baseline: doc.base,
        help: [
          '수집 시각(available_at) 기준 일별 canonical 뉴스 문서 수.',
          '',
          '판정: 응답의 기준값(최근 기준일들의 중앙값) 대비 ±25%(R13 과 같은 식).',
          '기준값은 규칙이 쓰는 기준선과 같은 값이다.',
          '',
          doc.base == null
            ? '⚠️ 이 응답은 기준(중앙값)을 주지 않았다 — 오늘 값만 그리고 판정하지 않는다.'
            : '오늘 값은 원장 실측이다. 일별 계열을 주는 응답이 없어 한 점만 그린다.',
        ].join('\n'),
        drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
      }
    : uninstrumented({
        id: 'n.documents',
        label: '뉴스 문서 수',
        group: 'news',
        unit: '건',
        metricType: 'volume',
        direction: 'stable',
        source: 'DB_LEDGER',
        help: OUTPUT_ABSENT('o.doc'),
        drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
      }),
  entityResolutionMetric(entityResolution),

  /* 분석 결과 — 단위가 같은 것끼리만 비율을 낸다 */
  res !== null
    ? {
        id: 'a.results',
        label: '분석 결과 생성 수',
        group: 'analysis',
        unit: '건',
        metricType: 'volume',
        comparisonType: 'medianDelta',
        threshold: 0.25,
        direction: 'stable',
        source: 'DB_LEDGER',
        series: onePoint(TODAY, res),
        help: [
          '일별 분석 결과(explanation_result) 생성 건수 — 배치 레인.',
          '',
          '오늘 값은 체인 관측에서 온다.',
          '',
          '판정: 중앙값 대비 ±25%로 보는 지표지만, 기준값과 일별 계열을 주는 응답이 없어 지금은 판정하지 않는다.',
        ].join('\n'),
        drill: { href: '/analyses', label: '분석 결과 목록' },
      }
    : uninstrumented({
        id: 'a.results',
        label: '분석 결과 생성 수',
        group: 'analysis',
        unit: '건',
        metricType: 'volume',
        direction: 'stable',
        source: 'DB_LEDGER',
        help: CHAIN_ABSENT,
        drill: { href: '/analyses', label: '분석 결과 목록' },
      }),
  /* 🔴 `chain('c.res') ?? 0` 이던 자리다. 부재를 0 으로 접으면 "결과 생성률 0%" 라는 거짓
   * 경보가 매일 뜬다 — 체인 축은 계측이 없어 실 응답에 아예 없다(계약 §축별 소스). */
  res !== null && run !== null && run !== 0
    ? {
        id: 'a.run_to_result',
        label: '분석 실행 → 결과 생성률',
        group: 'analysis',
        unit: '비율',
        metricType: 'rate',
        comparisonType: 'minRatio',
        threshold: 0.99,
        direction: 'higherIsBetter',
        source: 'DB_LEDGER',
        series: onePoint(TODAY, res / run),
        help: [
          '결과 생성률 = 분석 결과 / 분석 실행. **단위가 같은 두 값**이라 비율을 낸다.',
          `오늘 ${res} / ${run}.`,
          '',
          '판정: 최소 99% — 실행이 끝났는데 결과가 안 남는 것은 분포 문제가 아니라 결손이다.',
          '',
          '트리거 이벤트 수와는 단위가 달라 그쪽과는 비율을 만들지 않는다.',
        ].join('\n'),
        drill: { href: '/ops/chain', label: '설명 생성 흐름' },
      }
    : uninstrumented({
        id: 'a.run_to_result',
        label: '분석 실행 → 결과 생성률',
        group: 'analysis',
        unit: '비율',
        metricType: 'rate',
        direction: 'higherIsBetter',
        source: 'DB_LEDGER',
        /* 분모가 0 인 것도 판정 불가다 — `|| 1` 로 메우면 실행 0건인 날 100% 가 선다 */
        help: run === 0 ? '분석 실행이 0건인 날은 생성률이 정의되지 않는다 — 분모를 1로 메우지 않는다.' : CHAIN_ABSENT,
        drill: { href: '/ops/chain', label: '설명 생성 흐름' },
      }),
  f.etf_ledger
    ? {
        id: 'a.failed_etfs',
        label: '분석 실패 ETF',
        group: 'analysis',
        unit: '종',
        metricType: 'defect',
        comparisonType: 'maxCount',
        threshold: 0,
        direction: 'lowerIsBetter',
        source: f.etf_ledger.mock === true ? 'MOCK' : 'DB_LEDGER',
        series: onePoint(
          TODAY,
          f.etf_ledger.rows.filter((r) => r.outcome === 'FAILED').length,
          f.etf_ledger.mock === true,
        ),
        help: [
          '조회일 ETF별 최신 트리거의 최신 설명 실행이 실패로 끝난 ETF 수.',
          '',
          '판정: 1종 이상이면 이상.',
          ...(f.etf_ledger.mock === true
            ? ['⚠️ 이 응답의 ETF 원장은 목 데이터다.']
            : ['오늘 값은 설명 실행 원장에서 왔다. 일별 계열을 주는 응답이 없어 한 점만 그린다.']),
        ].join('\n'),
        drill: { href: '/analyses', label: '분석 결과 목록' },
      }
    : uninstrumented({
        id: 'a.failed_etfs',
        label: '분석 실패 ETF',
        group: 'analysis',
        unit: '종',
        metricType: 'defect',
        direction: 'lowerIsBetter',
        source: 'DB_LEDGER',
        help: [
          'per-ETF 분석 귀결 원장이 없어 실패 ETF 수를 셀 수 없다(계약 §축별 소스: `etf_ledger`).',
          '',
          '0 으로 그리면 "오늘 실패한 ETF 가 없다"가 되는데, 그건 이 응답이 답하지 못하는 말이다.',
        ].join('\n'),
        drill: { href: '/analyses', label: '분석 결과 목록' },
      }),
  trig
    ? {
        id: 'a.trigger_events',
        label: '배치 트리거 이벤트',
        group: 'analysis',
        unit: '건',
        metricType: 'volume',
        comparisonType: 'medianDelta',
        threshold: 0.25,
        direction: 'stable',
        source: 'DB_LEDGER',
        series: onePoint(TODAY, trig.today),
        baseline: trig.base,
        help: [
          '트리거 이벤트 수 — 분석 결과의 **보조 지표**다(일배치의 대표 지표가 아니다).',
          '',
          '단위 사슬: 트리거 이벤트 → 중복 제거된 분석 대상 → 분석 실행 → 생성 결과.',
          '같은 ETF 에서 트리거가 여러 번 날 수 있어 결과 수와 직접 비교하지 않는다.',
          '',
          '판정: 응답의 기준값(최근 기준일들의 중앙값) 대비 ±25%.',
          ...(trig.base == null
            ? ['', '⚠️ 이 응답은 기준(중앙값)을 주지 않았다 — 오늘 값만 실측이고 비교할 평소가 없다.']
            : []),
        ].join('\n'),
        drill: { href: '/ops/chain', label: '설명 생성 흐름' },
      }
    : uninstrumented({
        id: 'a.trigger_events',
        label: '배치 트리거 이벤트',
        group: 'analysis',
        unit: '건',
        metricType: 'volume',
        direction: 'stable',
        source: 'DB_LEDGER',
        help: OUTPUT_ABSENT('o.trig'),
        drill: { href: '/ops/chain', label: '설명 생성 흐름' },
      }),
  {
    id: 'a.trigger_to_target',
    label: '트리거 → 분석 대상 전환율',
    group: 'analysis',
    unit: '비율',
    metricType: 'rate',
    comparisonType: 'uninstrumented',
    threshold: null,
    direction: 'stable',
    source: 'CODE',
    series: [],
    help: [
      '트리거 이벤트가 몇 건의 **분석 대상**으로 접혔는지를 재려면 중복 제거된 대상 수가 필요하다.',
      '그 축을 남기는 계측이 없다 — 그래서 성공률을 지어내지 않고 계측 없음으로 둔다.',
      '',
      '필요한 계측: 트리거 → 분석 대상 dedupe 결과 수(현재 응답·원장 모두에 없음).',
    ].join('\n'),
    drill: { href: '/ops/chain', label: '설명 생성 흐름' },
  },
  ];
}
