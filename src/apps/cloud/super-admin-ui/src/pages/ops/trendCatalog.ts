/* 추이 지표 카탈로그 — facts 를 지표로 옮긴다 (ALPHA-738).
 *
 * 여기서만 사실을 읽는다(trendMetrics 는 순수 계산). 오늘 값은 가능한 한 facts 실측이고,
 * **일별 계열을 주는 응답이 없어** 과거 점은 구조화된 목이다 — 점마다 isMock 을 들고 다닌다.
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
import factsJson from '../../rules/facts-snapshot.json' with { type: 'json' };
/* 타입만 가져온다 — 런타임 import 면 JSX 모듈이 딸려 와 순수 테스트에서 못 읽는다 */
import type { ConsoleFacts } from './shared';
import { buildSeries } from './trendMetrics.ts';
import type { Metric } from './trendMetrics.ts';

const F = factsJson as unknown as ConsoleFacts;

const TODAY = F.meta.today;

/** 기준일 지연 — (actual, expected] 사이의 거래일 수. 두 날짜 모두 실측 필드다 */
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

const task = (key: string) => F.tasks.find((t) => t.task_key === key);
const dataset = (id: string) => F.datasets.find((d) => d.id === id);
const funnel = (stage: string) => F.news_funnel.find((s) => s.stage.startsWith(stage))?.value ?? null;
/* 축이 통째로 없으면 `null` — 부재를 0 으로 그리면 "결과 생성률 0%" 라는 거짓 경보가 된다
 * (`coverageMetric`·`lagMetric` 의 `comparisonType: 'uninstrumented'` 규약과 같은 방향). */
const chain = (id: string) => F.chain?.stages.find((s) => s.id === id)?.batch ?? null;
const output = (id: string) => F.outputs.find((o) => o.id === id);

/** 완전성 = received / expected. 분모가 없으면 null(0 이 아니다) */
function coverage(taskKey: string): { value: number | null; mock: boolean } {
  const t = task(taskKey);
  if (!t || t.completeness_expected == null || t.completeness_received == null) {
    return { value: null, mock: false };
  }
  return { value: t.completeness_received / t.completeness_expected, mock: t.cmpl_mock === true };
}

const dsDrill = (id: string) => ({ href: `/ops/datasets?focus=ds-${id}`, label: '데이터셋 상세' });

/* ── 일배치 ── 데이터셋마다 하나씩 */

function coverageMetric(
  id: string,
  label: string,
  taskKey: string,
  datasetId: string,
  denomLabel: string,
): Metric {
  const c = coverage(taskKey);
  const t = task(taskKey);
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
    isMock: true,
    series:
      c.value === null
        ? []
        : buildSeries({
            today: c.value,
            pin: Math.min(1, c.value * 1.001),
            amplitude: 0.05,
            todayIsMock: c.mock,
            endDate: TODAY,
            min: 0,
          }),
    help: [
      `완전성 = 적재 ${denomLabel} / 기대 ${denomLabel} (원장 completeness 축).`,
      t?.completeness_expected != null
        ? `오늘 ${t.completeness_received}/${t.completeness_expected} ${denomLabel}.`
        : '분모가 배선되지 않아 계산할 수 없다.',
      '',
      '판정: 계약 최소 95% 미만이면 기준 미달이다 — 산출량 지표의 ±25% 자를 쓰지 않는다.',
      '레코드 수가 아니라 엔티티 커버리지다(행 수는 하루 거래량에 따라 흔들린다).',
      '',
      '과거 계열은 응답에 없어 검수용 목이다 — 오늘 값만 원장에서 온다.',
    ].join('\n'),
    drill: dsDrill(datasetId),
  };
}

function lagMetric(id: string, label: string, datasetId: string): Metric {
  const d = dataset(datasetId);
  const lag = tradingLag(d?.actual_as_of ?? null, d?.expected_as_of ?? null);
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
    isMock: true,
    series:
      lag === null
        ? []
        : buildSeries({ today: lag, pin: 0, amplitude: 1, integer: true, min: 0, todayIsMock: false, endDate: TODAY }),
    help: [
      '기준일 지연 = 기대 기준일과 실제 기준일 사이의 거래일 수.',
      d?.expected_as_of ? `기대 ${d.expected_as_of} · 실제 ${d.actual_as_of ?? '없음'}.` : '',
      '',
      '판정: 허용 지연 0거래일 — 하루라도 밀리면 분석이 오래된 스냅샷 위에서 돈다.',
      '수집 시각(collected_at)과는 다른 축이다 — 오늘 수집해도 담긴 기준일은 어제일 수 있다.',
      '',
      '과거 계열은 응답에 없어 검수용 목이다.',
    ]
      .filter(Boolean)
      .join('\n'),
    drill: dsDrill(datasetId),
  };
}

/* ── 지표 목록 ── */

export const METRICS: Metric[] = [
  /* 일배치 */
  coverageMetric('b.price_daily', '가격 일봉 완전성', 'PRICE_COLLECTION_KIS', 'price_daily', '종목'),
  coverageMetric('b.etf_holdings', 'ETF 구성종목 완전성', 'ETF_HOLDINGS_COLLECTION_KRX', 'etf_holdings', 'ETF'),
  lagMetric('b.investor_flow', '수급 기준일 지연', 'investor_flow'),
  lagMetric('b.etf_nav', 'ETF NAV 기준일 지연', 'etf_nav'),
  {
    id: 'b.disclosures',
    label: '공시 문서 수',
    group: 'batch',
    unit: '건',
    metricType: 'volume',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'MOCK',
    isMock: true,
    /* 일별 공시 적재 건수를 주는 응답이 없다 — 계열 전체가 목이다 */
    series: buildSeries({ today: 41, pin: 38, amplitude: 1, integer: true, min: 0, todayIsMock: true, endDate: TODAY }),
    help: [
      '일별 공시 수집·적재 문서 수.',
      '',
      '판정: 직전 10영업일 중앙값 대비 ±25%(산출량 지표).',
      '공시는 제출량 자체가 요일·이벤트에 따라 흔들려서 완전성이 아니라 분포로 본다.',
      '',
      '⚠️ 오늘 값을 포함해 계열 전체가 검수용 목이다 — 일별 적재 건수를 주는 응답이 없다.',
    ].join('\n'),
    drill: dsDrill('disclosures'),
  },

  /* 장중 — 분 단위 원시 개수가 아니라 비율·지연·결손 수로 */
  {
    id: 'i.evidence_ratio',
    label: '1분 가격 · 증거 창 비율',
    group: 'intraday',
    unit: '비율',
    metricType: 'coverage',
    comparisonType: 'minRatio',
    threshold: 0.98,
    direction: 'higherIsBetter',
    source: 'MOCK',
    isMock: true,
    series: buildSeries({ today: 386 / 390, pin: 0.995, amplitude: 0.01, min: 0, todayIsMock: true, endDate: TODAY }),
    help: [
      '증거 창 비율 = 결과가 기록된 창 / 기대 창(390). 오늘은 386/390.',
      '',
      '판정: 최소 98% — 하루 8창 이상 비면 분석 입력이 흔들린다.',
      '빈 데이터(VALID_EMPTY)는 실행 증거가 있으므로 증거 창에 포함된다 — 무증거와 다른 사실이다.',
      '',
      '⚠️ 계열 전체가 검수용 목이다 — 장중 원장에 최근 7일 일별 요약 엔드포인트가 없다.',
    ].join('\n'),
    drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
  },
  {
    id: 'i.no_evidence',
    label: '1분 가격 · 무증거 창',
    group: 'intraday',
    unit: '창',
    metricType: 'defect',
    comparisonType: 'maxCount',
    threshold: 0,
    direction: 'lowerIsBetter',
    source: 'MOCK',
    isMock: true,
    series: buildSeries({ today: 4, pin: 0, amplitude: 1, integer: true, min: 0, todayIsMock: true, endDate: TODAY }),
    help: [
      '무증거 창 = 기한(window_end)이 지났는데 결과 증거가 없는 창(DUE 또는 유효 lease 없는 CLAIMED).',
      '',
      '판정: 1개 이상이면 이상 — 결손은 분포로 보지 않는다.',
      '이 수치만으로 미실행·실행체 사망을 확정하지 않는다.',
      '',
      '⚠️ 계열 전체가 검수용 목이다 — 일별 요약 엔드포인트가 없다.',
    ].join('\n'),
    drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
  },
  {
    id: 'i.watermark_delay',
    label: '1분 가격 · 연속 완결 지연',
    group: 'intraday',
    unit: '분',
    metricType: 'delay',
    comparisonType: 'maxDelayMinutes',
    threshold: 5,
    direction: 'lowerIsBetter',
    source: 'MOCK',
    isMock: true,
    series: buildSeries({ today: 3, pin: 2, amplitude: 1, integer: true, min: 0, todayIsMock: true, endDate: TODAY }),
    help: [
      '연속 완결 워터마크가 마지막 기록보다 얼마나 뒤처져 있는가(분).',
      '',
      '판정: 허용 지연 5분 초과면 이상.',
      '워터마크는 빈 창 없이 연속으로 완결된 지점이라, 뒤처짐은 중간에 구멍이 있다는 뜻이다.',
      '',
      '⚠️ 계열 전체가 검수용 목이다.',
    ].join('\n'),
    drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
  },
  {
    id: 'i.dead_jobs',
    label: '1분 가격 · DEAD job',
    group: 'intraday',
    unit: '건',
    metricType: 'defect',
    comparisonType: 'maxCount',
    threshold: 0,
    direction: 'lowerIsBetter',
    source: 'MOCK',
    isMock: true,
    series: buildSeries({ today: 2, pin: 0, amplitude: 1, integer: true, min: 0, todayIsMock: true, endDate: TODAY }),
    help: [
      '재시도가 소진된 처리 job 수(DB job 원장의 status=DEAD). 실제 큐 지표가 아니다.',
      '',
      '판정: 1건 이상이면 이상.',
      '이 응답에는 해소 축이 없어 당일 누적이다 — 이미 복구됐는지는 알 수 없다.',
      '',
      '⚠️ 계열 전체가 검수용 목이다.',
    ].join('\n'),
    drill: { href: `/minute?date=${TODAY}&dataset=price_minute`, label: '세션 상세' },
  },

  /* 뉴스 — 2~3개로. 단계별 감소는 퍼널이 답한다 */
  {
    id: 'n.documents',
    label: '뉴스 문서 수',
    group: 'news',
    unit: '건',
    metricType: 'volume',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: output('o.doc')!.today,
      /* 중앙값을 규칙(R13)이 쓰는 기준선에 고정한다 — 두 화면이 다른 편차를 말하면 안 된다 */
      pin: output('o.doc')!.base ?? undefined,
      integer: true,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      '수집 시각(available_at) 기준 일별 canonical 뉴스 문서 수.',
      '',
      '판정: 직전 10영업일 중앙값 대비 ±25%(R13 과 같은 식).',
      '중앙값은 이 계열에서 계산되며 규칙이 쓰는 기준선과 같은 값이다.',
      '',
      '과거 계열은 검수용 목이고 오늘 값은 원장 실측이다.',
    ].join('\n'),
    drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
  },
  {
    id: 'n.universe_rate',
    label: '뉴스 유니버스 매칭률',
    group: 'news',
    unit: '비율',
    metricType: 'rate',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: (funnel('유니버스 매칭') ?? 0) / (funnel('중복 제거') ?? 1),
      pin: 0.42,
      amplitude: 0.3,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      '유니버스 매칭률 = 유니버스 매칭 기사 / 중복 제거 후 canonical 기사.',
      `오늘 ${funnel('유니버스 매칭')?.toLocaleString('ko-KR')} / ${funnel('중복 제거')?.toLocaleString('ko-KR')}.`,
      '',
      '판정: 계약된 최소 비율이 없어 분포(중앙값 ±25%)로 본다.',
      '실질 탈락 단계는 여기다 — 앞 단계의 감소는 중복 제거·축 차이라 유실이 아니다.',
      '단계별 감소 자체는 데이터 화면의 뉴스 처리 퍼널이 답한다.',
      '',
      '과거 계열은 검수용 목이다.',
    ].join('\n'),
    drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
  },
  {
    id: 'n.assertion_rate',
    label: '뉴스 assertion 생성률',
    group: 'news',
    unit: '비율',
    metricType: 'rate',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: (funnel('assertion') ?? 0) / (funnel('RDS 문서') ?? 1),
      pin: 0.24,
      amplitude: 0.3,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      'assertion 생성률 = assertion 이 남은 문서 / RDS 문서.',
      `오늘 ${funnel('assertion')?.toLocaleString('ko-KR')} / ${funnel('RDS 문서')?.toLocaleString('ko-KR')}.`,
      '',
      '판정: 계약된 최소 비율이 없어 분포(중앙값 ±25%)로 본다.',
      '⚠️ 없음의 사유가 한 통이다 — NO_EVENT · 추출 실패 · 미실행을 가르는 계측이 없어,',
      '이 비율의 하락을 곧바로 실패로 읽지 않는다.',
      '',
      '과거 계열은 검수용 목이다.',
    ].join('\n'),
    drill: { href: '/ops/datasets?focus=news-funnel', label: '뉴스 처리 퍼널' },
  },

  /* 분석 결과 — 단위가 같은 것끼리만 비율을 낸다 */
  {
    id: 'a.results',
    label: '분석 결과 생성 수',
    group: 'analysis',
    unit: '건',
    metricType: 'volume',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: chain('c.res') ?? 0,
      pin: 22,
      integer: true,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      '일별 분석 결과(explanation_result) 생성 건수 — 배치 레인.',
      '',
      '판정: 직전 10영업일 중앙값 대비 ±25%.',
      '',
      '과거 계열은 검수용 목이고 오늘 값은 체인 관측에서 온다.',
    ].join('\n'),
    drill: { href: '/analyses', label: '분석 결과 목록' },
  },
  {
    id: 'a.run_to_result',
    label: '분석 실행 → 결과 생성률',
    group: 'analysis',
    unit: '비율',
    metricType: 'rate',
    comparisonType: 'minRatio',
    threshold: 0.99,
    direction: 'higherIsBetter',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: (chain('c.res') ?? 0) / (chain('c.run') || 1),
      pin: 1,
      amplitude: 0.02,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      '결과 생성률 = 분석 결과 / 분석 실행. **단위가 같은 두 값**이라 비율을 낸다.',
      `오늘 ${chain('c.res')} / ${chain('c.run')}.`,
      '',
      '판정: 최소 99% — 실행이 끝났는데 결과가 안 남는 것은 분포 문제가 아니라 결손이다.',
      '',
      '트리거 이벤트 수와는 단위가 달라 그쪽과는 비율을 만들지 않는다.',
    ].join('\n'),
    drill: { href: '/ops/chain', label: '설명 생성 흐름' },
  },
  {
    id: 'a.failed_etfs',
    label: '분석 실패 ETF',
    group: 'analysis',
    unit: '종',
    metricType: 'defect',
    comparisonType: 'maxCount',
    threshold: 0,
    direction: 'lowerIsBetter',
    source: 'MOCK',
    isMock: true,
    series: buildSeries({
      today: F.etf_ledger?.rows.filter((r) => r.outcome === 'FAILED').length ?? 0,
      pin: 0,
      integer: true,
      min: 0,
      todayIsMock: F.etf_ledger?.mock === true,
      endDate: TODAY,
    }),
    help: [
      '분석이 실패로 끝난 ETF 수(per-ETF 분석 원장).',
      '',
      '판정: 1종 이상이면 이상.',
      '⚠️ per-ETF 원장이 계측되지 않아 이 값은 목이다 — 개별 ops task 가 없고 SFN·stdout·DB',
      '결과로 간접 관측한다.',
    ].join('\n'),
    drill: { href: '/analyses', label: '분석 결과 목록' },
  },
  {
    id: 'a.trigger_events',
    label: '배치 트리거 이벤트',
    group: 'analysis',
    unit: '건',
    metricType: 'volume',
    comparisonType: 'medianDelta',
    threshold: 0.25,
    direction: 'stable',
    source: 'DB_LEDGER',
    isMock: true,
    series: buildSeries({
      today: output('o.trig')!.today,
      pin: output('o.trig')!.base ?? undefined,
      min: 0,
      todayIsMock: false,
      endDate: TODAY,
    }),
    help: [
      '트리거 이벤트 수 — 분석 결과의 **보조 지표**다(일배치의 대표 지표가 아니다).',
      '',
      '단위 사슬: 트리거 이벤트 → 중복 제거된 분석 대상 → 분석 실행 → 생성 결과.',
      '같은 ETF 에서 트리거가 여러 번 날 수 있어 결과 수와 직접 비교하지 않는다.',
      '',
      '판정: 직전 10영업일 중앙값 대비 ±25%.',
    ].join('\n'),
    drill: { href: '/ops/chain', label: '설명 생성 흐름' },
  },
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
    isMock: false,
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
