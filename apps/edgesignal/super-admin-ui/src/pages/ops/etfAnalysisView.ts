/* 흐름 화면의 ETF 표 — 새 분석 엔진의 ETF별 귀결을 어떻게 보여 줄지의 판단(ALPHA-1312).
 *
 * 화면 파일에 두지 않는다: 정렬·요약·부재 처리는 테스트할 수 있어야 한다. 이 모듈은 **보여 주는
 * 순서와 문구**만 정한다 — 상태는 서버가 정하고(`etfAnalyses`), 판정은 규칙이 한다(R15·R10).
 */
import type { EtfAnalysisRow, Facts, MovementState } from '../../rules/types';

export const STATE_LABEL: Record<MovementState, string> = {
  completed: '완료',
  failed: '실패',
  running: '진행 중',
  not_started: '시작 못 함',
  pending: '판정 보류',
  none: '대상 아님',
};

/** 먼저 봐야 하는 순서 — 문제(실패·시작 못 함), 아직 안 끝난 것(진행 중·판정 보류), 나머지 */
const RANK: Record<MovementState, number> = {
  failed: 0,
  not_started: 1,
  running: 2,
  pending: 3,
  completed: 4,
  none: 5,
};

export type StateTone = 'bad' | 'wait' | 'ok' | 'none';

export function stateTone(state: MovementState): StateTone {
  if (state === 'failed' || state === 'not_started') return 'bad';
  if (state === 'running' || state === 'pending') return 'wait';
  return state === 'completed' ? 'ok' : 'none';
}

const worst = (r: EtfAnalysisRow) => Math.min(RANK[r.outlook], RANK[r.movement]);
const other = (r: EtfAnalysisRow) => Math.max(RANK[r.outlook], RANK[r.movement]);

/** ETF 한 칸의 색은 두 종류 가운데 먼저 봐야 하는 쪽을 따른다 */
export function rowTone(row: EtfAnalysisRow): StateTone {
  return stateTone(RANK[row.outlook] <= RANK[row.movement] ? row.outlook : row.movement);
}

export interface EtfTable {
  /** 문제 있는 ETF 가 먼저 — 같은 순위면 다른 종류의 상태, 그다음 코드 순이다 */
  rows: EtfAnalysisRow[];
  /** 표 머리의 요약 — 예: "148종 · 실패 2 · 시작 못 함 1" */
  summary: string;
}

/**
 * 표의 모델. **새 축이 없으면 `null`** 이다 — 화면은 "기록 없음"을 그린다.
 *
 * 인자가 새 축 하나뿐인 것이 계약이다: 옛 축(`etf_ledger`)을 받지 않으므로, 새 축이 없는 응답에서
 * 옛 엔진의 값으로 표를 대신 그릴 길이 없다.
 */
export function etfTable(analyses: Facts['etf_analyses']): EtfTable | null {
  if (!analyses) return null;
  const rows = [...analyses.rows].sort(
    (a, b) => worst(a) - worst(b) || other(a) - other(b) || a.etf.localeCompare(b.etf),
  );
  /* ETF 수로 센다 — 한 ETF 가 두 종류 모두 실패여도 실패 1 이다 */
  const having = (s: MovementState) => rows.filter((r) => r.outlook === s || r.movement === s).length;
  const parts = [`${rows.length}종`, `실패 ${having('failed')}`, `시작 못 함 ${having('not_started')}`];
  /* 아직 안 끝난 것은 있을 때만 적는다 — 하루가 끝난 뒤에는 늘 0 이다 */
  if (having('running')) parts.push(`진행 중 ${having('running')}`);
  if (having('pending')) parts.push(`판정 보류 ${having('pending')}`);
  return { rows, summary: parts.join(' · ') };
}
