/* 가격 판정 근거 표현 모델(§33.12 로컬).
 *
 * 화면은 기록이 말하는 것만 옮긴다 — 기록 없음을 무발화로, 이력 없음을 최신 세대로 채우지 않고,
 * judged_at 을 순서로 쓰지 않는다. 계산(메모리)과 반영(트랜잭션 확정)은 끝까지 다른 칸이다.
 */
import type { MinuteJudgmentAttempt, MinuteJudgmentBaseline, MinuteJudgmentWindow } from './types';

export const NO_RECORD = '기록 없음 — 무발화·미실행으로 추정하지 않습니다';
export const RECOMPUTATION_NOTE =
  '정정 후 재계산은 제공하지 않습니다 — 순차 처리가 따로 보장된 구간이 아니면 결과를 보장할 수 없습니다(재계산 보장 불가).';
export const JUDGED_AT_NOTE = 'judged_at 은 기록 시점의 관측 시각입니다. 커밋 순서·인과 순서가 아닙니다.';
export const SCOPE_NOTE =
  '이 절은 저장된 판정 근거(판정 당시 입력 버전·checksum 기록)의 조회입니다. 현재 보관된 원본 본문을 읽어 존재·무결성을 다시 검증하지 않습니다.';

const has = (s: Record<string, string[]>, key: string, e: string) => (s[key] ?? []).includes(e);

/** 메모리에서 계산한 판정 */
export function computed(s: Record<string, string[]>, e: string): string {
  if (has(s, 'error_entities', e)) return '판정 불가(오류)';
  if (has(s, 'fired', e)) return '발화';
  if (has(s, 'revert_candidates', e)) return '회수';
  if (has(s, 'stale_snapshot', e)) return '건너뜀(읽은 앵커가 더 늦은 window)';
  if (has(s, 'skipped_no_open', e)) return '건너뜀(기준선 없음)';
  return '무발화';
}

/** 트랜잭션에서 실제로 확정된 결과 */
export function committed(s: Record<string, string[]>, e: string): string {
  if (has(s, 'inserted', e)) return '트리거 확정';
  if (has(s, 'stale_tx', e)) return '차단(tx 에서 더 늦은 앵커 확인)';
  if (has(s, 'fired', e)) return '무시(같은 window 트리거가 이미 있음)';
  if (has(s, 'reverted', e)) return '회수 확정';
  if (has(s, 'revert_candidates', e)) return '회수 무효(조건부 갱신 0행)';
  return '없음';
}

/** 이 판정에 나온 종목 — 요약 목록·읽은 앵커·tx 관측의 합집합(오류 문자열 목록은 제외) */
export function involved(a: MinuteJudgmentAttempt): string[] {
  const ids = new Set<string>([...Object.keys(a.anchorsUsed), ...Object.keys(a.txAnchor)]);
  for (const [key, list] of Object.entries(a.summary)) {
    if (key !== 'errors') for (const e of list) ids.add(e);   // errors 는 문장, error_entities 가 종목
  }
  return [...ids].sort();
}

export function kstHhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString('ko-KR', {
    timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function readAnchor(a: MinuteJudgmentAttempt, e: string): string {
  const used = a.anchorsUsed[e];
  return used ? `${Number(used[0])} @ ${kstHhmm(used[1])}` : '없음(기준선을 앵커로 씀)';
}

/** 관측 시각은 window 와 날짜가 다를 수 있어 날짜까지 보인다(순서·인과가 아니다) */
export function kstObserved(iso: string): string {
  return new Date(iso).toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: false,
  });
}

/** 긴 식별자는 앞부분만 보이고 전체는 title 로 남긴다 */
export function abbreviate(value: string, keep = 12): string {
  return value.length > keep + 1 ? `${value.slice(0, keep)}…` : value;
}

export function txObservation(a: MinuteJudgmentAttempt, e: string): string {
  if (!(e in a.txAnchor)) return '관측 안 함';
  const w = a.txAnchor[e];
  const where = w ? kstHhmm(w) : '앵커 행 없음';
  const changed = (a.anchorsUsed[e]?.[1] ?? null) !== w;
  const how = a.txAnchorLocked ? '잠금 뒤 관측' : '비잠금 관측';
  return `${where} (${how}${changed ? ' · 읽은 뒤 바뀜' : ''})`;
}

export function baseline(b: MinuteJudgmentBaseline | undefined): string {
  if (!b) return '기준선 기록 없음';
  if (b.ref === 'pre-record') return `${Number(b.value)} · 기록 도입 전 확정된 시가(출처 세대 미상)`;
  const src = b.source === 'prev_close' ? `전일 종가(기준일 ${b.ref})` : `시가 폴백(${b.ref})`;
  return `${Number(b.value)} · ${src}`;
}

export function inputLabel(w: MinuteJudgmentWindow): string {
  const rec = w.inputRecord === 'RECORDED'
    ? `판정 당시 입력: 세대 ${w.jobGeneration} · artifact 이력 기록 있음 · 원본 본문 재검증 안 함`
    : `판정 당시 입력: 세대 ${w.jobGeneration} · artifact 이력 없음(이력 부재 — 원본 삭제·손상을 뜻하지 않음) · 현재 세대로 대체하지 않음`;
  return w.correctedAfter ? `${rec} · 이후 세대 ${w.windowGeneration}로 정정됨` : rec;
}

/** 판정 불가(종목별 형상 오류) — 정상 무발화와 구분해 보여 준다 */
export function judgmentErrors(a: MinuteJudgmentAttempt): string[] {
  return a.summary.errors ?? [];
}
