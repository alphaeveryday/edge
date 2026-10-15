/* screening 도메인 — 정책 기준의 표시 어휘(뷰 관심사). 어휘 SSOT 는 여기다.
 *
 * 같은 기준이 두 화면에 나온다: 점검 처리 기준 표의 **설정** 값과, 검수 상세의 **검수 사유**다.
 * 운영자가 "확신도 중간 이하"로 걸었으면 걸린 사유도 같은 말이어야 매핑이 필요 없다
 * (ALPHA-774). 한쪽에 하드코딩하면 다음 문구 변경에서 갈라진다 — 이 프로젝트에서 이미
 * 여러 번 겪은 패턴이라 함수 하나로 묶는다.
 *
 * 극성은 **걸리는 쪽**이다("1개 이하면 걸린다"). 정책 저장값은 자동 제공 임계(min)라
 * 여기서 뒤집어 읽는다 — 버전 이력 표는 자동 제공 기준의 기록이라 반대 극성을 쓴다.
 */

/** 출처 수 기준(min_source_count) → 걸리는 조건 문구. 어휘 밖 값은 원값을 보인다. */
export function sourceGateLabel(minSources: number | null | undefined): string | null {
  if (minSources == null) return null;
  if (minSources <= 1) return '출처 없음';
  return `${minSources - 1}개 이하`;
}

/** 확신도 기준(min_confidence) → 걸리는 조건 문구. */
export function confidenceGateLabel(minConfidence: string | null | undefined): string | null {
  if (minConfidence == null) return null;
  return CONFIDENCE_GATE[minConfidence] ?? minConfidence;
}

/**
 * 검수 사유용 전체 문구 — 설정 문구에 축 이름을 붙인다. 설정 화면은 행 라벨이 "출처 수"라
 * 옵션이 값만 지지만(`1개 이하`), 사유는 홀로 서므로 축이 필요하다. 다만 `출처 없음` 처럼
 * 이미 축을 품은 문구에는 붙이지 않는다 — "출처 수 출처 없음"이 된다.
 */
export function sourceGateReason(minSources: number | null | undefined): string | null {
  const gate = sourceGateLabel(minSources);
  if (gate == null) return null;
  return gate.startsWith('출처') ? gate : `출처 수 ${gate}`;
}

/** 확신도 사유 문구 — `확신도 보류 이하`. 값 쪽이 축을 품지 않아 단순 접두다. */
export function confidenceGateReason(minConfidence: string | null | undefined): string | null {
  const gate = confidenceGateLabel(minConfidence);
  return gate == null ? null : `확신도 ${gate}`;
}

// 정책 어휘는 MEDIUM|HIGH 뿐이다(DB CHECK·ALPHA-634). 어휘 밖 값이 오면 위에서 원값이 나간다.
const CONFIDENCE_GATE: Record<string, string> = {
  MEDIUM: '보류 이하',
  HIGH: '중간 이하',
};
