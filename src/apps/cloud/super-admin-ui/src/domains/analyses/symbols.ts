/* 분석 결과 → 종목별 묶음 (ALPHA-738).
 *
 * 장중에 같은 종목의 분석이 여러 번 생성돼도 **덮어쓰지 않고 이력으로 남는다**. 그래서 기본
 * 목록을 시간순으로 평평하게 깔면 같은 종목이 반복돼 읽히지 않는다 — 종목당 한 행으로 접고,
 * 전체 이력은 별도 보기로 둔다.
 *
 * ⚠️ **최신은 완료 시각으로 정하지 않는다.** 과거 기준의 분석이 늦게 끝났다고 더 최신 기준의
 * 설명을 덮으면 안 된다. 우선순위는 (1) 변동 기준 시각 (2) 같은 기준이면 결정적 실행 순서(id)다.
 *
 * ⚠️ **최신 시도가 실패해도 이전 유효 설명을 지우지 않는다.** 둘은 다른 축이라 따로 들고 다닌다:
 *   latestValid   — 결과가 남은 가장 최신 기준의 분석(운영자가 읽을 설명)
 *   latestAttempt — 기준 시각이 가장 최신인 시도(실패했을 수 있다)
 *
 * 여기서 만드는 값은 전부 응답 필드의 파생이다 — 없는 축을 지어내지 않는다.
 */
import type { Analysis } from './types.ts';

export interface SymbolGroup {
  /** `${market}:${code}` — 라우트 키와 같은 축 */
  key: string;
  code: string;
  name: string;
  market: Analysis['market'];
  /** 이 종목의 분석 전량(최신 기준 시각 순) */
  analyses: Analysis[];
  /** 결과가 남은 가장 최신 기준의 분석. 없으면 null */
  latestValid: Analysis | null;
  /** 기준 시각이 가장 최신인 시도 — 실패·진행 중일 수 있다 */
  latestAttempt: Analysis;
  /** 최신 시도가 유효 결과가 아니다(실패 또는 진행 중) */
  attemptPending: boolean;
  todayCount: number;
}

/**
 * 결과가 실제로 남았는가 — 상태와 본문 둘 다 본다(실패에 본문이 있으면 그건 데이터 결함이다).
 *
 * ⚠️ **본문은 두 자리에 있다.** `result` 는 `analysis_result.summary`, `resultBlocks` 는
 * `stage_results -> final_explanation -> blocks` 로 **서버에서 독립된 두 컬럼**이다
 * (`JdbcAnalysisRepository` LIST_SQL). 상세 화면은 블록이 있으면 그걸 고객 산문으로 그리고
 * `result` 는 폴백일 뿐이라, `result` 만 보면 **블록만 있는 완료 분석이 "결과 없음"** 이 되어
 * 목록에서 사라지고 `attemptPending` 이 거짓으로 켜진다.
 */
export function hasResult(a: Analysis): boolean {
  if (a.status !== 'COMPLETED') return false;
  return a.result.trim().length > 0 || (a.resultBlocks?.length ?? 0) > 0;
}

export const symbolKey = (a: Pick<Analysis, 'market' | 'code'>) => `${a.market}:${a.code}`;

/**
 * 최신순 정렬 — 기준 시각(내림차순) → 같으면 id(내림차순, 결정적 실행 순서 대용).
 * 완료 시각은 정렬 축이 아니다.
 */
export function byBasisDesc(a: Analysis, b: Analysis): number {
  if (a.basisTimeAbs !== b.basisTimeAbs) return a.basisTimeAbs < b.basisTimeAbs ? 1 : -1;
  return a.id < b.id ? 1 : -1;
}

export function groupBySymbol(items: Analysis[]): SymbolGroup[] {
  const by = new Map<string, Analysis[]>();
  for (const a of items) {
    const k = symbolKey(a);
    if (!by.has(k)) by.set(k, []);
    by.get(k)!.push(a);
  }
  const groups: SymbolGroup[] = [];
  for (const [key, list] of by) {
    const analyses = [...list].sort(byBasisDesc);
    const latestAttempt = analyses[0];
    const latestValid = analyses.find(hasResult) ?? null;
    groups.push({
      key,
      code: latestAttempt.code,
      name: latestAttempt.name,
      market: latestAttempt.market,
      analyses,
      latestValid,
      latestAttempt,
      attemptPending: !hasResult(latestAttempt),
      todayCount: analyses.length,
    });
  }
  /* 종목 정렬도 결정적으로 — 최신 시도 기준 시각순, 같으면 코드순 */
  return groups.sort(
    (x, y) => byBasisDesc(x.latestAttempt, y.latestAttempt) || x.code.localeCompare(y.code),
  );
}

export function findGroup(items: Analysis[], market: string, code: string): SymbolGroup | undefined {
  return groupBySymbol(items).find((g) => g.market === market && g.code === code);
}
