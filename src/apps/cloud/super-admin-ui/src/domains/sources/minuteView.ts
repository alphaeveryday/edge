/* 장중 1분 세션 → 화면 표현 모델 (ALPHA-738 재설계).
 *
 * 왜 순수 모듈인가: "무증거와 정상 빈 데이터는 다른 사실"이라는 계약이 화면 배치를 바꿔도
 * 살아 있어야 한다. 렌더링에 섞어 두면 그 의도를 테스트할 방법이 없다.
 *
 * 지키는 선:
 *   · 서버 판정(leaseExpired · overdueNoEvidence · noEvidence · claimedExpired)을 다시 정의하지
 *     않는다. 여기서는 **묶어 보여줄 뿐** 재계산하지 않는다.
 *   · 실행 축(세션 생존) · 창 축(데이터 상태) · job 축(큐)은 서로 다른 축이라 합성 상태로
 *     뭉개지 않는다. 데이터 쪽은 합성 판정 대신 **사실 배지**만 낸다.
 *   · 응답이 답할 수 없는 것은 만들지 않는다 — 아래 UNKNOWABLE 참고.
 *
 * 응답만으로 알 수 없는 것(숫자를 지어내지 않는 지점):
 *   1. **도래한 창 수**. overdueNoEvidence 술어는 live lease 를 가진 CLAIMED 를 일부러 뺀다
 *      (방금 닫힌 창을 정상 수집 중인 상태). 그래서 due+claimed 중 몇이 이미 도래했는지
 *      응답으로 못 가른다 — evidenced+missing+overdueNoEvidence 는 **하한**일 뿐이라
 *      진행률 분모로 쓰지 않는다.
 *   2. **분별 전체 타임라인**. gaps 는 결함·무증거 창만 준다. 정상·미도래 창은 분별 행이 없다.
 *   3. **DEAD 의 해소 여부**. 당일 누적 카운트뿐이라 "지금 장애"라고 단정할 수 없다.
 */
import type { MinuteGapWindow, MinuteJobCounts, MinuteSession } from './types.ts';

/** ui-kit BadgeTone 과 같은 어휘지만 모듈이 UI 를 import 하지 않도록 여기서 재선언한다 */
export type ViewTone = 'active' | 'neutral' | 'gated' | 'warn' | 'blocked' | 'env';

/* ── 실행 축 ── */

export type LivenessKind = 'live' | 'broken' | 'closing' | 'unknown' | 'failed';

export interface Liveness {
  kind: LivenessKind;
  label: string;
  tone: ViewTone;
  /** 판정 근거 — 배지 옆 보조 문구가 아니라 툴팁·도움말용 */
  basis: string;
}

/**
 * 실행체 생존 — leaseExpired 는 서버(DB 시계) 판정이고 여기선 라벨만 고른다.
 * phase 를 무시하면 정상 종료·과거 날짜 세션이 전부 "증거 끊김"으로 보인다.
 */
export function liveness(s: MinuteSession): Liveness {
  if (s.phase === 'FAILED') {
    return { kind: 'failed', label: '세션 FAILED', tone: 'blocked', basis: 'phase=FAILED' };
  }
  if (s.phase === 'DRAINED' || s.phase === 'QC_RUNNING' || s.phase === 'FINALIZED') {
    return {
      kind: 'closing',
      label: '종료 국면',
      tone: 'gated',
      basis: `phase=${s.phase} — drain 이후 실행체는 떠나는 게 정상이라 생존 판정 대상이 아니다`,
    };
  }
  if (s.leaseExpired === true) {
    return {
      kind: 'broken',
      label: '실행 증거 끊김',
      tone: 'blocked',
      basis: 'lease 만료(서버 DB 시계 판정) — 실행체가 갱신을 멈췄다',
    };
  }
  if (s.leaseExpired === false) {
    return { kind: 'live', label: '실행 정상', tone: 'active', basis: 'lease 유효 · heartbeat 갱신 중' };
  }
  return {
    kind: 'unknown',
    label: '기동 증거 없음',
    tone: 'neutral',
    basis: 'lease 부재(NULL) — "죽었다"가 아니라 기동 증거 자체가 없다는 사실',
  };
}

/* ── 창 축 ── */

export type SegmentKey =
  | 'valid'
  | 'validEmpty'
  | 'incomplete'
  | 'invalid'
  | 'missing'
  | 'noEvidence'
  | 'pending'
  | 'unmaterialized';

export interface Segment {
  key: SegmentKey;
  label: string;
  count: number;
  /** 색맹·흑백 출력에서도 갈리도록 색과 별개의 무늬 축을 준다 */
  pattern: 'solid' | 'hatch' | 'dot' | 'open';
  tone: ViewTone;
  meaning: string;
}

/**
 * 분별 전체 타임라인 대신 쓰는 구간 요약. 각 조각은 응답이 실제로 준 카운트이고,
 * 합은 expectedWindowCount 다 — 없는 분모를 만들지 않는다.
 *
 * `pending` 은 "미도래"와 "수집 중"을 **응답이 못 가르는** 한 통이다. 이 둘을 갈라 진행률을
 * 그리려면 서버가 도래 여부를 내려줘야 한다(minuteViewApiGaps 참고).
 */
export function segments(s: MinuteSession): Segment[] {
  const w = s.windows;
  const pending = Math.max(0, w.due + w.claimed - w.overdueNoEvidence);
  const unmaterialized = Math.max(0, s.expectedWindowCount - materializedCount(s));
  const all: Segment[] = [
    { key: 'valid', label: '정상', count: w.valid, pattern: 'solid', tone: 'active', meaning: '수집 결과가 남았고 검증을 통과한 창' },
    {
      key: 'validEmpty',
      label: '정상 · 빈 데이터',
      count: w.validEmpty,
      pattern: 'dot',
      tone: 'active',
      meaning:
        '돌았는데 그 분에 거래가 없었다는 **증거가 남은** 창 — 정상 결과다. 무증거(안 돌았음)와 다른 사실이라 합쳐 세지 않는다.',
    },
    { key: 'incomplete', label: '불완전', count: w.incomplete, pattern: 'solid', tone: 'warn', meaning: '결과는 남았으나 스텝이 유실을 판정한 창' },
    { key: 'invalid', label: '무효', count: w.invalid, pattern: 'solid', tone: 'blocked', meaning: '결과가 남았으나 무효로 판정된 창' },
    { key: 'missing', label: 'MISSING (EOD 판정)', count: w.missing, pattern: 'solid', tone: 'blocked', meaning: 'EOD QC 가 결손으로 판정한 창 — 장중에는 매겨지지 않는다' },
    {
      key: 'noEvidence',
      label: '무증거',
      count: w.overdueNoEvidence,
      pattern: 'hatch',
      tone: 'blocked',
      meaning:
        '기한(window_end)이 지났는데 결과가 안 적힌 창 — 안 돌았거나 실행체가 죽었다. 서버(DB 시계) 판정이다.',
    },
    {
      key: 'pending',
      label: '미도래 · 수집 중',
      count: pending,
      pattern: 'open',
      tone: 'neutral',
      meaning:
        '아직 기한이 안 온 창과 유효 lease 로 수집 중인 창이 한 통이다 — 이 응답은 둘을 가르지 않는다. 결함이 아니다.',
    },
    {
      key: 'unmaterialized',
      label: '창 행 없음',
      count: unmaterialized,
      pattern: 'open',
      tone: 'warn',
      meaning: '기대 창 수에는 있는데 원장에 행이 없다 — 어떤 집계에도 안 잡히는 materialize 결손 후보',
    },
  ];
  return all.filter((seg) => seg.count > 0);
}

/** 원장에 실재하는 창 행 수. 기대 창 수와 다르면 위 숫자들을 그대로 믿으면 안 된다. */
export function materializedCount(s: MinuteSession): number {
  const w = s.windows;
  return w.due + w.claimed + w.valid + w.validEmpty + w.incomplete + w.missing + w.invalid;
}

/** 실행 증거가 남은 창 — 진행률 분모가 아니라 그 자체로 하나의 사실이다 */
export function evidencedCount(s: MinuteSession): number {
  const w = s.windows;
  return w.valid + w.validEmpty + w.incomplete + w.invalid;
}

/** 품질 결함 창 — 증거는 남았지만 정상이 아닌 것 + EOD 결손. 무증거는 여기 넣지 않는다. */
export function qualityDefectCount(s: MinuteSession): number {
  const w = s.windows;
  return w.incomplete + w.invalid + w.missing;
}

/* ── 결손 구간 ── */

export interface GapRun {
  /** 연속한 창 묶음의 시작·끝 (ISO) */
  from: string;
  to: string;
  count: number;
  dataStatus: string;
  noEvidence: boolean;
}

/**
 * 결손 창을 **연속 구간**으로 접는다 — "언제부터 어느 정도"에 답하는 형태다.
 * 접는 기준은 (상태, noEvidence) 가 같고 앞 창의 끝이 다음 창의 시작과 맞닿을 때뿐이다.
 * 상태가 다르면 절대 합치지 않는다(무증거와 불완전을 한 구간으로 뭉개면 안 된다).
 */
export function gapRuns(gaps: MinuteGapWindow[]): GapRun[] {
  const sorted = [...gaps].sort((a, b) => a.windowStart.localeCompare(b.windowStart));
  const runs: GapRun[] = [];
  for (const g of sorted) {
    const last = runs[runs.length - 1];
    if (
      last &&
      last.dataStatus === g.dataStatus &&
      last.noEvidence === g.noEvidence &&
      last.to === g.windowStart
    ) {
      last.to = g.windowEnd;
      last.count += 1;
      continue;
    }
    runs.push({
      from: g.windowStart,
      to: g.windowEnd,
      count: 1,
      dataStatus: g.dataStatus,
      noEvidence: g.noEvidence,
    });
  }
  return runs;
}

/* ── 현재 확인할 항목 ── */

export type IssueKey =
  | 'liveness'
  | 'noEvidence'
  | 'quality'
  | 'ledgerMismatch'
  | 'claimedExpired'
  | 'dead';

export interface Issue {
  key: IssueKey;
  title: string;
  /** 좁은 표면(첫 화면 한 줄)용 짧은 이름 — 제목을 잘라 쓰면 괄호가 반쯤 남는다 */
  short: string;
  /** 단위가 종류마다 다르므로 숫자와 단위를 함께 낸다 */
  count: number;
  unit: string;
  tone: ViewTone;
  /** 영향 시각 범위 — 응답이 시각을 주지 않는 항목은 null */
  range: { from: string; to: string } | null;
  detail: string;
}

/**
 * 운영자가 지금 확인해야 하는 예외만. 정상 창은 여기 오지 않는다.
 *
 * DEAD 는 당일 누적이고 해소 축이 응답에 없다 — "지금 장애"라고 단정하지 않고 사실대로 쓴다.
 */
export function issues(s: MinuteSession, jobs: MinuteJobCounts): Issue[] {
  const out: Issue[] = [];
  const live = liveness(s);
  const w = s.windows;

  if (live.kind === 'broken' || live.kind === 'failed') {
    out.push({
      key: 'liveness',
      title: live.label,
      short: live.label,
      count: 1,
      unit: '세션',
      tone: 'blocked',
      range: null,
      detail: live.basis,
    });
  }

  const noEvidenceGaps = s.gaps.filter((g) => g.noEvidence);
  if (w.overdueNoEvidence > 0) {
    out.push({
      key: 'noEvidence',
      title: '무증거 창 — 기한이 지났는데 결과가 없다',
      short: '무증거',
      count: w.overdueNoEvidence,
      unit: '창(1분)',
      tone: 'blocked',
      range: rangeOf(noEvidenceGaps),
      detail:
        '안 돌았거나 실행체가 죽은 창이다. 돌았는데 거래가 없었던 창(정상 · 빈 데이터)과 다른 사실이라 합치지 않는다.',
    });
  }

  const qualityGaps = s.gaps.filter((g) => !g.noEvidence);
  const quality = qualityDefectCount(s);
  if (quality > 0) {
    out.push({
      key: 'quality',
      title: '품질 결함 창 — 불완전 · 무효 · MISSING',
      short: '품질 결함',
      count: quality,
      unit: '창(1분)',
      tone: 'warn',
      range: rangeOf(qualityGaps),
      detail: '결과는 남았지만 정상이 아닌 창과 EOD QC 가 결손으로 판정한 창이다.',
    });
  }

  const materialized = materializedCount(s);
  if (materialized !== s.expectedWindowCount) {
    out.push({
      key: 'ledgerMismatch',
      title: '원장 불일치 — 기대 창 수와 실재 행 수가 다르다',
      short: '원장 불일치',
      count: Math.abs(s.expectedWindowCount - materialized),
      unit: '창(1분)',
      tone: 'blocked',
      range: null,
      detail: `기대 ${s.expectedWindowCount} vs 실재 ${materialized}. 행이 없는 창은 무증거를 포함한 어떤 집계에도 안 잡히므로 위 숫자들을 그대로 믿으면 안 된다.`,
    });
  }

  if (jobs.claimedExpired > 0) {
    out.push({
      key: 'claimedExpired',
      title: '유효 lease 없이 claimed 인 job',
      short: '큐 고착',
      count: jobs.claimedExpired,
      unit: 'job',
      tone: 'blocked',
      range: null,
      detail:
        'Consumer 가 죽고 아무도 재청구하지 않은 고착 후보다(만료·NULL 포함 — writer 의 회수 조건과 같은 집합). "처리 중"에 뭉개면 영원히 경고가 없다.',
    });
  }

  if (jobs.dead > 0) {
    out.push({
      key: 'dead',
      title: 'DEAD job (당일 누적 · 해소 여부 미기록)',
      short: 'DEAD job',
      count: jobs.dead,
      unit: 'job',
      tone: 'warn',
      range: null,
      detail:
        '재시도가 소진된 job 이다. 이 응답에는 해소 축이 없어 이미 복구됐는지 알 수 없다 — 그것만으로 지금 장애라고 단정하지 않는다.',
    });
  }

  return out;
}

function rangeOf(gaps: MinuteGapWindow[]): { from: string; to: string } | null {
  if (gaps.length === 0) return null;
  const starts = gaps.map((g) => g.windowStart).sort();
  const ends = gaps.map((g) => g.windowEnd).sort();
  return { from: starts[0], to: ends[ends.length - 1] };
}

/**
 * 이 화면이 정확해지려면 서버가 더 줘야 하는 것 — 화면에 그대로 노출해 부채를 감추지 않는다.
 * (구현은 이 작업 범위 밖이다. 프론트에서 추정해 메우지 않는다.)
 */
export const MINUTE_API_GAPS: { need: string; why: string }[] = [
  {
    need: 'windows.elapsed (또는 claimedLive) 1개 필드',
    why: 'overdueNoEvidence 가 live lease CLAIMED 를 빼기 때문에 "도래한 창"을 응답으로 못 센다 — 진행률 분모가 없다.',
  },
  {
    need: '분별 상태 압축 배열 (예: windowSegments[{from,to,status}])',
    why: 'gaps 는 결함·무증거 창만 준다. 정상·미도래 창의 분별 행이 없어 전체 타임라인·히트맵을 만들 수 없다.',
  },
  {
    need: 'job DEAD 의 해소 축 (resolvedAt 또는 unresolved 카운트)',
    why: '당일 누적 DEAD 만으로는 이미 복구된 과거 실패와 지금 막힌 것을 가를 수 없다.',
  },
  {
    need: '세션 개시·종료 시각 (sessionStart · sessionEnd)',
    why: '창 축의 시간 범위를 응답이 주지 않아 "장 시작~종료" 축을 그릴 근거가 없다.',
  },
];
