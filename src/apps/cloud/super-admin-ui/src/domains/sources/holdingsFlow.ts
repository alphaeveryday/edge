/* ETF 구성종목 데이터셋의 최종 완전성 — 조건부 결손 진입 판정 (ALPHA-738).
 *
 * **왜 수집 작업 하나에 걸지 않는가**: 결손 상세가 계산하는 값은
 * `기대(Planner snapshot) − 기준 거래일 현재 적재` 이지 수집 작업 한 건의 결과가 아니다.
 * 그래서 링크는 수집 셀이 아니라 **데이터셋 문맥**(수집 → 정제 → 적재 → 최종 완전성)에 건다.
 *
 * 지키는 선:
 *   · 어디서 탈락했는지 단정하지 않는다 — 수집·정제·적재 중 원인을 고르지 않는다.
 *   · 적재가 안 끝났으면 차집합이 전부 누락으로 보인다 → **확정 결손처럼 보이지 않게** 한다.
 *   · 기대 목록이 없으면 "결손 없음"이 아니라 **계산 불가**다.
 */
import type { TaskStatus } from './types.ts';

/** 이 데이터셋을 이루는 작업들 — 카탈로그(datasetCatalog `etf_holdings`)와 같은 집합이다 */
export const HOLDINGS_STEPS: { stage: string; taskKey: string; label: string }[] = [
  { stage: 'raw', taskKey: 'ETF_HOLDINGS_COLLECTION_KRX', label: '수집' },
  { stage: 'normalize', taskKey: 'NORMALIZE_ETF', label: '정제' },
  { stage: 'feature', taskKey: 'LOAD_ETF_HOLDINGS', label: '적재' },
];

/**
 * 최종 완전성 상태.
 *   missing — 결손이 있다(누락 상세 액션을 연다)
 *   pending — 적재가 아직 안 끝났다(판정 대기 — 확정 결손으로 그리지 않는다)
 *   unknown — 완전성 분모가 없다(계산 불가 — 결손 없음이 아니다)
 *   none    — 결손 없음(액션을 숨긴다)
 *   absent  — 이 런에 구성종목 작업 자체가 없다(블록을 세우지 않는다)
 */
export type HoldingsFlowState = 'missing' | 'pending' | 'unknown' | 'none' | 'absent';

export interface HoldingsFlow {
  state: HoldingsFlowState;
  /** 상태 판정에 실제로 쓴 근거 — 화면이 그대로 보여준다(추정 문구를 만들지 않는다) */
  basis: string;
  /** 완전성 대조 — 분모가 없으면 null */
  completeness: { expected: number | null; received: number | null; missing: number | null } | null;
  /** 이 데이터셋에 속한 작업들(있는 것만, HOLDINGS_STEPS 순서) */
  steps: { label: string; task: TaskStatus }[];
}

const isPending = (t: TaskStatus | undefined) =>
  t !== undefined &&
  (t.outcome === 'PENDING' || t.outcome === null) &&
  t.planStatus !== 'SKIPPED';

/**
 * 실행 원장의 작업 목록에서 이 데이터셋의 흐름과 최종 상태를 읽는다.
 * 판정에 쓰는 신호는 전부 원장 값이다: 수집 completeness · 적재 dataStatus · 적재 failedRecords.
 */
export function holdingsFlow(tasks: TaskStatus[]): HoldingsFlow {
  const steps = HOLDINGS_STEPS.map((s) => ({
    label: s.label,
    task: tasks.find((t) => t.taskKey === s.taskKey),
  })).filter((s): s is { label: string; task: TaskStatus } => s.task !== undefined);

  if (steps.length === 0) {
    return { state: 'absent', basis: '이 런에 구성종목 작업이 없다', completeness: null, steps: [] };
  }

  const collect = tasks.find((t) => t.taskKey === 'ETF_HOLDINGS_COLLECTION_KRX');
  const load = tasks.find((t) => t.taskKey === 'LOAD_ETF_HOLDINGS');
  const completeness = collect?.completeness ?? null;

  /* 적재가 아직 안 끝났으면 차집합이 전부 누락으로 보인다 — 확정하지 않는다 */
  if (isPending(load)) {
    return {
      state: 'pending',
      basis: '적재(LOAD_ETF_HOLDINGS)가 아직 귀결되지 않아 결손을 확정할 수 없다',
      completeness,
      steps,
    };
  }

  const missingCount = completeness?.missing ?? null;
  const loadDefect =
    load?.dataStatus === 'INCOMPLETE' ||
    load?.dataStatus === 'INVALID' ||
    (load?.failedRecords ?? 0) > 0;

  if ((missingCount ?? 0) > 0 || loadDefect) {
    const reasons = [
      (missingCount ?? 0) > 0 ? `수집 완전성 누락 ${missingCount}` : null,
      load?.dataStatus === 'INCOMPLETE' || load?.dataStatus === 'INVALID'
        ? `적재 데이터 판정 ${load.dataStatus}`
        : null,
      (load?.failedRecords ?? 0) > 0 ? `적재 유실 ${load!.failedRecords}` : null,
    ].filter(Boolean);
    return {
      state: 'missing',
      basis: `${reasons.join(' · ')} — 어느 단계에서 탈락했는지는 단정하지 않는다`,
      completeness,
      steps,
    };
  }

  /* 분모가 없으면 "결손 없음"이 아니라 계산 불가다 */
  if (completeness === null || completeness.expected === null) {
    return {
      state: 'unknown',
      basis: '완전성 분모(기대 ETF 수)가 없어 누락을 계산할 수 없다 — 결손 없음이 아니다',
      completeness,
      steps,
    };
  }

  return { state: 'none', basis: '기대 ETF 가 모두 적재됐다', completeness, steps };
}
