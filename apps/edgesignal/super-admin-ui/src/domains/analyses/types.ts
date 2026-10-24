/* analyses 도메인 — 가격 변동 분석 (공통 분석 산출물 운영). 원장(explanation_*) 실조회 계약. */

export type AnalysisMarket = 'KRX' | 'NASDAQ';

/** 원장 confidence_level 어휘 그대로 — 화면이 새 판정을 만들지 않는다. */
export type AnalysisConfidence = 'HIGH' | 'MEDIUM' | 'LOW';

export type AnalysisStatus =
  | 'COMPLETED' // 분석 완료
  | 'PENDING' // 분석 대기 (근거 데이터 수집 중)
  | 'FAILED'; // 분석 실패 (재시도 큐 등록)

/** 게시 수명주기 — 실행 상태와 별개 축. 무효화 버튼은 PUBLISHED 에서만 활성(ALPHA-737). */
export type AnalysisPublicationStatus = 'DRAFT' | 'PUBLISHED' | 'WITHDRAWN';

/** 근거 유형 코드 — 선언 순서가 표시 순서다(근거 포맷 명세 §1). 저장·전송은 영문 코드, 한글은 labels.ts. */
export type EvidenceType = 'PRICE' | 'HOLDING' | 'DISCLOSURE' | 'NEWS' | 'FINANCIAL' | 'STAT_TEST';

/** 통계검정 method — 무엇과 무엇을 비교했나(명세 §3.3). */
export type StatMethod =
  | 'SIMILAR_STOCKS'
  | 'SIMILAR_DAYS'
  | 'SENSITIVE_STOCKS'
  | 'RELATED_STOCKS'
  | 'BY_CONDITION'
  | 'VS_USUAL';

/** 통계검정 basis — 무엇의 움직임을 설명하는 주장인가(명세 §3.2). */
export type StatBasis = 'MARKET' | 'SECTOR' | 'IDIO';

/** 통계검정 band — 오늘 값이 과거 표본 분포 안에서 어디인가(명세 §3.6). */
export type StatBand = 'TOP_TAIL' | 'UPPER' | 'MIDDLE' | 'LOWER' | 'BOTTOM_TAIL';

export type StatUnit = 'COUNT' | 'DAY';

/**
 * 통계검정 행의 추가정보(명세 §3.4·§10.4) — 접힌 행 아래 펼쳐지는 영역.
 * 데이터 배선(C6)은 아직이라 지금 API 는 이 필드를 채우지 않는다 — 타입·렌더 자리만 선행.
 */
export interface StatTestDetail {
  basis: StatBasis;
  method: StatMethod;
  /** 표본 크기 — unit 과 함께 '과거 41건' / '과거 14일' */
  n: number;
  unit: StatUnit;
  /** 평균 차이 — 비율로 저장, 렌더가 ×100 해 %p 로 그린다(명세 §4) */
  estimate: number;
  /** 유의확률 */
  p: number;
  /** 같이 검정한 가설 수 — 1이면 보정 조각을 그리지 않는다 */
  k?: number;
  /** 과거 분포 내 위치 — 값이 없으면 줄 자체를 생략한다 */
  band?: StatBand;
  /** 검정이 읽은 데이터 이름들 — '·' 로 이어 출처 줄이 된다(명세 §3.7) */
  series: string[];
}

export interface AnalysisEvidence {
  /**
   * EvidenceType 영문 코드. string 인 이유: UI·API 가 따로 배포돼 코드 전환 전 API 는
   * 번역된 한글('뉴스')을 보낼 수 있다 — 라벨 매핑은 코드만 히트하고 나머지는 원문 폴백.
   */
  type: string;
  title: string;
  source: string;
  time: string;
  /** 통계검정(STAT_TEST) 행에만 실린다 — 플랫 5종은 없다 */
  detail?: StatTestDetail;
}

/**
 * 고객 산문에 실제로 나간 블록(final_explanation.blocks, ALPHA-878) — 내부 산출(stat_tests
 * 버퍼·stage_results 원시값)은 API 가 애초에 싣지 않는다. 순서가 곧 고객이 본 순서다.
 */
export interface AnalysisResultBlock {
  code: string;
  title: string;
  text: string;
  /** 이 문장이 참조한 근거 조회키(evidence_refs) — 문장 아래에 그대로 붙인다 */
  evidenceRefs: string[];
}

export interface Analysis {
  id: string;
  name: string;
  code: string;
  market: AnalysisMarket;
  direction: 1 | -1;
  /** 등락률(%) 절대값 */
  changePct: number;
  status: AnalysisStatus;
  /** 변동 기준 시각 (목록 표시용 축약) */
  basisTime: string;
  basisTimeAbs: string;
  /** 분석 완료 시각 — 미완료면 '—' */
  doneTime: string;
  /** 분석 신뢰도 — 결과가 아직 없으면 null */
  confidence: AnalysisConfidence | null;
  /** 게시 상태 — 결과가 아직 없는 런이면 null */
  publicationStatus: AnalysisPublicationStatus | null;
  result: string;
  /**
   * 고객 노출 문장 블록 — 있으면 result 원문 대신 이 블록들이 산문 카드가 된다.
   * optional 인 이유: UI·API 가 따로 배포돼 아직 안 주는 응답을 만날 수 있다.
   */
  resultBlocks?: AnalysisResultBlock[];
  evidence: AnalysisEvidence[];
  /**
   * 이 분석의 근거 총 건수 — evidence 는 표시 상한까지만 담기므로 더 클 수 있다.
   * optional 인 이유는 UI·API 가 따로 배포돼 **총 건수를 아직 안 주는 응답**을 만날 수
   * 있어서다 — 타입이 필수라고 말하면 소비자가 폴백 없이 숫자로 다뤄 NaN 이 새어 나간다.
   */
  evidenceTotal?: number;
}
