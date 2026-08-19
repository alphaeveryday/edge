/* 산출·품질 추이 지표 카탈로그 (ALPHA-738).
 *
 * 답하는 질문: **주요 데이터셋의 산출량과 품질이 평소 또는 운영 기준에서 벗어났는가.**
 *
 * 지키는 선:
 *   · **판정 규칙이 지표마다 다르다.** 완전성 95% 미만과 뉴스 문서 −35% 를 같은 ±25% 자로 재면
 *     둘 다 틀린다. 그래서 지표가 `comparisonType` + `threshold` 를 들고 다니고, 문구는
 *     하드코딩이 아니라 그 값에서 계산된다.
 *   · **중앙값·정상 범위·편차·판정은 전부 같은 계열에서 나온다.** 오늘 값과 중앙값을 따로
 *     들고 있으면 그래프와 숫자가 어긋난다.
 *   · **계측 없음은 정상도 실패도 아니다.** 분모가 없으면 비율을 지어내지 않는다.
 *   · 이 화면의 범위는 파이프라인 실행·데이터셋 산출/완전성·장중 수집·분석 결과 생성까지다.
 *     Cloud 게시·테넌트 발번·소비자 전달은 전달 화면 소관이라 지표로 두지 않는다.
 *
 * 실측과 목: 일별 API가 없는 지표는 과거 점을 구조화된 목으로 만들고, 일별 API가 있는 지표는
 * 실측 계열을 그대로 준다. 점마다 `isMock` 을 들고 다니고 카드가 그 사실을 배지로 낸다.
 */
import type { FactSource } from '../../rules/types.ts';
import { median } from './trendSeries.ts';

export type MetricGroup = 'batch' | 'intraday' | 'news' | 'analysis';

export const GROUP_LABEL: Record<MetricGroup, string> = {
  batch: '일배치',
  intraday: '장중',
  news: '뉴스',
  analysis: '분석 결과',
};

/** 무엇을 재는가 — 단위·포맷을 정한다 */
export type MetricType = 'volume' | 'coverage' | 'rate' | 'lag' | 'delay' | 'defect';

/**
 * 어떻게 판정하는가. **모든 지표에 ±25% 를 적용하지 않는다.**
 *   medianDelta      — 직전 N영업일 중앙값 대비 편차(산출량)
 *   minRatio         — 계약된 최소 비율 미만이면 이상(완전성·커버리지)
 *   maxCount         — 임계 초과면 이상(무증거·실패 수)
 *   maxLagDays       — 허용 거래일 지연 초과
 *   maxDelayMinutes  — 허용 지연 시간 초과(워터마크)
 *   uninstrumented   — 계산 근거가 없다. 정상으로도 실패로도 그리지 않는다
 */
export type ComparisonType =
  | 'medianDelta'
  | 'minRatio'
  | 'maxCount'
  | 'maxLagDays'
  | 'maxDelayMinutes'
  | 'uninstrumented';

export type Direction = 'higherIsBetter' | 'lowerIsBetter' | 'stable';

export interface SeriesPoint {
  date: string;
  value: number;
  /** 이 점이 목인가 — 오늘 점만 실측인 지표가 많다 */
  isMock: boolean;
}

export interface Metric {
  id: string;
  label: string;
  group: MetricGroup;
  /** 무엇을 세는 단위인가 — 다른 지표와 절대 섞지 않는다 */
  unit: string;
  metricType: MetricType;
  comparisonType: ComparisonType;
  /** 판정 임계. medianDelta 는 비율(0.25), minRatio 는 비율, 나머지는 절대값. 없으면 null */
  threshold: number | null;
  direction: Direction;
  /** 이 지표의 이상 톤. 생략하면 기존 판정처럼 blocked다. */
  abnormalTone?: 'warn' | 'blocked';
  source: FactSource;
  /* 계열에 목이 섞였는지는 **점마다**(`SeriesPoint.isMock`) 안다 — 지표 단위 플래그를 따로
   * 두지 않는다. 소비자가 0이었고, 두 벌이면 갈린다: 기준(`base`)이 없는 날의 계열은 실측
   * 한 점뿐인데 지표 플래그는 `true` 로 남아 카드가 없는 목을 말한다. */
  series: SeriesPoint[];
  /** 집계 단위·정의 — 팝오버 본문 */
  help: string;
  drill: { href: string; label: string };
}

export interface Verdict {
  kind: 'normal' | 'abnormal' | 'uninstrumented';
  label: string;
  tone: 'active' | 'warn' | 'blocked' | 'neutral';
  /** 판정 근거 한 줄 — 구조화 값에서 만든다 */
  basis: string;
  /** 오늘 값(계열의 마지막). 계열이 없으면 null */
  actual: number | null;
  /** 비교 기준값(중앙값 또는 임계) */
  expected: number | null;
  /** medianDelta 에서만 값이 있다 */
  deltaPct: number | null;
  /** 정상 범위 [하한, 상한] — 그래프 음영. 없으면 null */
  band: [number, number] | null;
  /** 이상이 어느 방향인가 */
  direction: 'up' | 'down' | null;
}

/* ── 값 표시 ── */

export function formatValue(m: Metric, v: number | null): string {
  if (v === null) return '—';
  if (m.metricType === 'coverage' || m.metricType === 'rate') return `${(v * 100).toFixed(1)}%`;
  return v.toLocaleString('ko-KR');
}

/* ── 판정 ── 문구는 전부 구조화 값에서 만든다 */

export function evaluateMetric(m: Metric): Verdict {
  const history = m.series.slice(0, -1).map((p) => p.value);
  const actual = m.series.length > 0 ? m.series[m.series.length - 1].value : null;

  if (m.comparisonType === 'uninstrumented' || actual === null) {
    return {
      kind: 'uninstrumented',
      label: '계측 없음',
      tone: 'neutral',
      basis: '판정에 필요한 분모·계열이 없다 — 정상으로도 실패로도 세지 않는다.',
      actual,
      expected: null,
      deltaPct: null,
      band: null,
      direction: null,
    };
  }

  const th = m.threshold;

  if (m.comparisonType === 'medianDelta') {
    const base = median(history);
    if (base === null || base === 0 || th === null) {
      return {
        kind: 'uninstrumented',
        label: '기준 없음',
        tone: 'neutral',
        basis: '중앙값을 낼 계열이 없어 편차를 계산하지 않는다.',
        actual,
        expected: null,
        deltaPct: null,
        band: null,
        direction: null,
      };
    }
    const ratio = (actual - base) / base;
    const pct = Math.round(ratio * 100);
    const out = Math.abs(ratio) >= th;
    return {
      kind: out ? 'abnormal' : 'normal',
      label: out ? `분포 밖 · ${pct > 0 ? '증가' : '감소'}` : '정상 범위',
      tone: out ? (m.abnormalTone ?? 'blocked') : 'active',
      basis: `직전 ${history.length}영업일 중앙값 대비 ${pct > 0 ? '+' : ''}${pct}% (임계 ±${Math.round(th * 100)}%)`,
      actual,
      expected: base,
      deltaPct: pct,
      band: [base * (1 - th), base * (1 + th)],
      direction: pct > 0 ? 'up' : pct < 0 ? 'down' : null,
    };
  }

  if (th === null) {
    return {
      kind: 'uninstrumented',
      label: '임계 없음',
      tone: 'neutral',
      basis: '이 지표의 운영 임계가 선언되지 않았다 — 판정 대상이 아니다.',
      actual,
      expected: null,
      deltaPct: null,
      band: null,
      direction: null,
    };
  }

  if (m.comparisonType === 'minRatio') {
    const out = actual < th;
    return {
      kind: out ? 'abnormal' : 'normal',
      label: out ? '기준 미달' : '기준 충족',
      tone: out ? (m.abnormalTone ?? 'blocked') : 'active',
      basis: `${(actual * 100).toFixed(1)}% — 최소 ${(th * 100).toFixed(0)}% 기준 ${out ? '미달' : '충족'}`,
      actual,
      expected: th,
      deltaPct: null,
      /* 정상 범위는 임계 위쪽 전부다 — 음영의 위 끝은 계열 최댓값이 정한다 */
      band: [th, Math.max(actual, ...history, th)],
      direction: out ? 'down' : null,
    };
  }

  const overLabel: Record<string, [string, string]> = {
    maxCount: ['임계 초과', '임계 이하'],
    maxLagDays: ['허용 지연 초과', '허용 지연 이내'],
    maxDelayMinutes: ['허용 지연 초과', '허용 지연 이내'],
  };
  const unitWord =
    m.comparisonType === 'maxLagDays' ? '거래일' : m.comparisonType === 'maxDelayMinutes' ? '분' : m.unit;
  const out = actual > th;
  const [bad, good] = overLabel[m.comparisonType] ?? ['임계 초과', '임계 이하'];
  return {
    kind: out ? 'abnormal' : 'normal',
    label: out ? bad : good,
    tone: out ? (m.abnormalTone ?? 'blocked') : 'active',
    basis: `${actual.toLocaleString('ko-KR')}${unitWord} — 허용 ${th.toLocaleString('ko-KR')}${unitWord} ${out ? '초과' : '이내'}`,
    actual,
    expected: th,
    deltaPct: null,
    /* 정상 범위는 임계 아래 전부 */
    band: [Math.min(0, ...history, actual), th],
    direction: out ? 'up' : null,
  };
}

/* ── 계열 만들기 ──
 *
 * 일별 계열을 주는 응답이 없다. 그래서 과거 점은 구조화된 목이되 **오늘 점은 실측**이고,
 * 중앙값·정상 범위·편차는 전부 이 계열에서 계산된다(따로 들고 있으면 어긋난다).
 */

/**
 * 거래일 축 — 주말을 건너뛰며 뒤로 센다. 고정 계산이라 새로고침해도 흔들리지 않는다.
 *
 * 🔴 **마지막 점은 `endISO` 그 자체다 — 주말이어도 건너뛰지 않는다.** `endISO` 는 `meta.today`
 * 이고 그건 "응답이 실제로 본 날"이라 거래일 보장이 없다(`rules/types.ts` 의 `meta` 계약, 그리고
 * `?date=` 로 아무 날짜나 들어온다). 건너뛰면 그 날 원장에서 온 **실측값이 전 영업일 날짜를
 * 달고**, `TrendPage` 의 규약("카드 날짜가 조회일과 다르면 그 지표는 응답 밖 축")이 그것을
 * 정적 스냅샷으로 읽게 한다 — 값이 관대해지는 게 아니라 **출처가 뒤바뀐다**.
 * 과거 점만 영업일로 센다.
 */
export function businessDays(endISO: string, count: number): string[] {
  const out = [endISO];
  const d = new Date(`${endISO}T00:00:00Z`);
  while (out.length < count) {
    d.setUTCDate(d.getUTCDate() - 1);
    const day = d.getUTCDay();
    if (day !== 0 && day !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out.reverse();
}

/**
 * 과거 계열의 모양. 가운데 두 점을 **정확히 `pin`** 으로 두면 median(history) === pin 이
 * 반올림·부동소수 오차 없이 성립한다 — 규칙(R13)이 쓰는 중앙값과 화면의 중앙값을 맞출 때 쓴다.
 */
const SHAPE = [-0.14, -0.09, 0.11, -0.05, 0, 0, 0.06, 0.14, -0.12, 0.09];

export interface SeriesSpec {
  today: number;
  /** 과거 계열의 중앙값을 이 값으로 고정한다. 없으면 today 기준으로 만든다 */
  pin?: number;
  /** 흔들림 폭 배율 — 완전성처럼 좁게 움직이는 지표는 작게 준다 */
  amplitude?: number;
  /** 정수 단위 지표는 반올림한다(창 수·건수) */
  integer?: boolean;
  /** 값의 하한(예: 0 미만 결손 수는 없다) */
  min?: number;
  /** 오늘 값이 실측인가 */
  todayIsMock: boolean;
  endDate: string;
}

export function buildSeries(spec: SeriesSpec): SeriesPoint[] {
  const pin = spec.pin ?? spec.today;
  const amp = spec.amplitude ?? 1;
  const dates = businessDays(spec.endDate, SHAPE.length + 1);
  const history = SHAPE.map((k) => {
    /* 가운데 두 점(k === 0)은 흔들지 않는다 — 그래야 중앙값이 정확히 pin 이다 */
    const raw = k === 0 ? pin : pin * (1 + k * amp);
    const v = spec.integer ? Math.round(raw) : raw;
    return spec.min !== undefined ? Math.max(spec.min, v) : v;
  });
  return [
    ...history.map((value, i) => ({ date: dates[i], value, isMock: true })),
    { date: dates[dates.length - 1], value: spec.today, isMock: spec.todayIsMock },
  ];
}
