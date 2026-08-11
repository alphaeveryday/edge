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
 * **본문은 두 자리에 있다.** `result` 는 `explanation_result.summary`, `resultBlocks` 는
 * `stage_results -> final_explanation -> blocks` 로 서버에서 **같은 행의 다른 컬럼**이다
 * (`JdbcAnalysisRepository` LIST_SQL — `res` 는 LEFT JOIN). 상세 화면은 블록이 있으면 그걸
 * 고객 산문으로 그리고 `result` 는 폴백이라, 배열 길이만 세면 `text` 가 빈 블록도 본문으로
 * 선다 — 서버 파서가 text 를 검증하지 않으므로 **비어 있지 않은 text 하나**를 요구한다.
 *
 * 🔴 **가릴 수 없는 자리가 하나 남는다.** 완료 런에 결과가 없으면 서버가 `result` 를 빈 채로
 * 주지 않고 안내 문장으로 바꿔 보낸다(`AnalysisResponse.result` — "설명 본문이 원장에
 * 없습니다 …"). 그래서 **실 응답의 `result` 는 절대 비지 않고**, 이 술어는 그 런을 유효
 * 결과로 센다. 서버가 "없음"을 문장으로 평탄화한 결과라 화면 쪽에서 되돌릴 수 없다 —
 * 한글 문구를 매칭하면 서버 카피에 결합되고, 같은 행에서 오는 `confidence`·
 * `publicationStatus` 는 둘 다 nullable 이라 판별자가 못 된다(결과가 있는데도 null 일 수
 * 있어, 그걸로 가르면 **진짜 설명을 숨긴다**). 축을 서버가 내려 줘야 닫힌다.
 */
export function hasResult(a: Analysis): boolean {
  if (a.status !== 'COMPLETED') return false;
  if (a.resultBlocks?.some((b) => b.text.trim().length > 0)) return true;
  return a.result.trim().length > 0;
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
