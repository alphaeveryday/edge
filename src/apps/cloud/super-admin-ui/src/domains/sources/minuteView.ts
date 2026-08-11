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

/* ── 데이터셋 축 ── */

/**
 * 1분 원장의 데이터셋. 어휘 정본은 `data_pipeline/minute/states.py` 의
 * `DATASET_PRICE_MINUTE`·`DATASET_NEWS_MINUTE` 다.
 *
 * **둘은 같은 세션·창 원장을 쓰지만 같은 판정 단위가 아니다.** 장중은 실행 시간대이지
 * 판정 단위가 아니라서, 두 데이터셋을 하나의 정상/장애로 합치지 않는다. 같은 컬럼이
 * 말하는 사실도 다르다(`commit.py` `commit_news_window` 의 판정 규칙):
 *   · VALID_EMPTY — 가격은 "그 분에 거래가 없었다", 뉴스는 "그 분에 신규 기사가 없었다"
 *   · INCOMPLETE  — 가격은 unit 유실, 뉴스는 poll 이 직전 성공 anchor 에 못 닿고 잘린 것
 *                   (truncated = 따라잡기 예약, 곧 lag 신호다)
 *   · universe    — 뉴스 세션은 소스 단위라 universe 축이 없다(원장에 `'none'` 이 박힌다)
 *
 * 어휘 밖 dataset 은 'other' 로 둔다 — 모르는 것을 가격으로 접으면 없는 의미가 붙는다.
 */
export type DatasetKind = 'price' | 'news' | 'disclosure' | 'other';

/**
 * 날짜 축 job 원장을 쓰는 데이터셋 — `news_extraction_job` 에는 `session_id` 도 `session_date` 도
 * 없고 `created_at` 뿐이다(가격 job 은 `session_id` 를 가진다). 그래서 이 데이터셋의 유실은
 * **세션이 아니라 날짜**에 매달리고, 세션이 없는 날에도 값이 있을 수 있다.
 * 이름을 상수로 두는 이유: 세션 목록에서 유도할 수 없는 자리(어댑터)가 있다.
 */
export const NEWS_MINUTE_DATASET = 'news_minute';

/**
 * 공시(ALPHA-875) — **window 단위 산출물이 없는 dataset 이다.** 증분 커서가 없어 매 tick 이
 * 그날 날짜창 전체를 다시 읽으므로, window 는 "그 분에 한 번 폴링했다"는 원장 단위다
 * (`minute/states.py` DATASET_DISCLOSURE_MINUTE 주석). 뉴스와 같은 성질이라 같이 읽는다.
 */
export const DISCLOSURE_MINUTE_DATASET = 'disclosure_minute';

export function datasetKind(dataset: string): DatasetKind {
  if (dataset === 'price_minute') return 'price';
  if (dataset === NEWS_MINUTE_DATASET) return 'news';
  if (dataset === DISCLOSURE_MINUTE_DATASET) return 'disclosure';
  return 'other';
}

/**
 * 1분이 **수집 창**인가 **poll 1회**인가 — 단위 명사를 정하는 술어다.
 * 세 자리(단위 이름·건강도·이슈 목록)가 각자 `=== 'news'` 를 적고 있었다. 레인이 늘 때
 * 한 곳만 고치면 나머지가 조용히 가격 어휘로 남으므로 술어를 한 곳에 둔다.
 */
export const isPollLane = (kind: DatasetKind): boolean =>
  kind === 'news' || kind === 'disclosure';

/** 창 축의 단위 이름 — 뉴스·공시의 1분은 수집 창이 아니라 **poll 1회**로 읽힌다 */
export function windowUnit(kind: DatasetKind): string {
  return isPollLane(kind) ? 'poll(1분)' : '창(1분)';
}

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
      basis:
        'lease 만료 — 서버(DB 시계) 판정이다. 근거는 lease_expires_at 이 now() 보다 앞선다는 사실뿐이고, ' +
        '실행체가 죽었는지 배포·네트워크 때문인지는 이 응답이 답하지 않는다.',
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

/* ── 세션 건강도 — 네 축을 합치되 무엇 때문인지 잃지 않는다 ── */

/**
 * `실행 정상` 하나로 수집기 생존·진행·커버리지·품질을 합치면, 수집기가 살아 있는데 결함이
 * 있는 상태가 초록으로 보인다. 그래서 **네 축을 따로 재고** 가장 나쁜 축으로 등급을 정하되
 * 이유를 함께 낸다.
 *
 * ⚠️ 커버리지 분모는 **기한이 도래한 창**이다. 거래일 전체 기대 창(390)을 분모로 쓰면 아직
 * 오지 않은 창이 결손처럼 보인다. 응답은 도래 여부를 직접 주지 않으므로
 * `증거 있는 창 + 무증거 창` 을 **하한**으로 쓴다(유효 lease 로 수집 중인 창은 아직 결과가
 * 없을 뿐이라 어느 쪽에도 넣지 않는다).
 */
export type SessionHealthKind = 'normal' | 'caution' | 'failure' | 'waiting' | 'closed';

export interface SessionHealth {
  kind: SessionHealthKind;
  label: string;
  tone: ViewTone;
  /** 왜 이 등급인가 — 가장 나쁜 축의 사실 */
  reason: string;
  /** 수집기 생존 */
  liveness: string;
  /** 진행 — 워터마크와 마지막 기록 */
  progress: string;
  /** 커버리지 — 기한 도래분 대비 증거 */
  coverage: { elapsed: number; evidenced: number; text: string };
  /** 품질 — 결함·무증거·큐 고착 */
  quality: { defects: number; text: string };
}

/** ISO 의 시:분만 — 응답이 이미 KST 오프셋을 달고 오므로 자르는 것이 결정적이다(locale 불요) */
const hhmmOf = (iso: string | null) => (iso ? iso.slice(11, 16) : '—');

export function sessionHealth(s: MinuteSession, jobs: MinuteJobCounts): SessionHealth {
  const w = s.windows;
  const kind = datasetKind(s.dataset);
  const noun = isPollLane(kind) ? 'poll' : '창';
  const evidenced = evidencedCount(s);
  /* 도래한 창의 하한 — 미도래·수집 중은 분모에 넣지 않는다.
   * ⚠️ **MISSING 은 넣는다.** EOD reconciliation 이 결손으로 판정한 창은 기한이 확실히
   * 지났다. 빼면 389증거 + 1MISSING 인 세션이 `기한 도래 389 중 증거 389` 로 서서 커버리지가
   * 만점으로 보이는데, 같은 창을 `qualityDefectCount` 는 결함으로 센다 — 한 화면이 같은
   * 창을 두 번 다르게 말한다. 마감·정산된 세션일수록 이 과대평가가 커진다. */
  const elapsed = evidenced + w.overdueNoEvidence + w.missing;
  const defects = qualityDefectCount(s);
  const stuck = jobs.claimedExpired + jobs.dead;
  /* 기대 창 수와 원장 실재 행 수가 다르다 — 위 숫자들을 그대로 믿으면 안 된다는 사실이다.
   * `issues()` 는 이걸 내는데 여기서 안 보면 요약이 "정상"이라 하고 상세가 "원장 수를 못
   * 믿는다"고 해 둘이 어긋난다. 장애로 세우지는 않는다 — 세션이 죽은 게 아니라 **셈의
   * 근거가 흔들리는** 것이다.
   *
   * ⚠️ **양방향이다.** `issues()` 가 `materialized !== expected` 로 재므로 여기서
   * `max(0, expected - materialized)` 로 재면 **행이 더 많은 쪽**(391 vs 390)을 0으로 접어
   * 요약만 깨끗해진다. 초과도 같은 사실이다 — 중복 materialize 든 기대 수 계산 오류든,
   * 어느 쪽이어도 창 집계를 그대로 믿을 수 없다. */
  const materialized = materializedCount(s);
  const ledgerGap = Math.abs(s.expectedWindowCount - materialized);

  const coverage = {
    elapsed,
    evidenced,
    text: `기한 도래 ${elapsed}${noun} 중 증거 ${evidenced}`,
  };
  const quality = {
    defects,
    text:
      kind === 'news'
        ? `잘린 poll·격리 ${defects} · 처리 대기 ${jobs.waiting} · DEAD ${jobs.dead}`
        : `품질 결함 ${defects} · DEAD ${jobs.dead}`,
  };
  const progress =
    kind === 'news'
      ? `최근 성공 poll ${hhmmOf(s.processedThrough)}`
      : `연속 완결 ${hhmmOf(s.contiguousCompleteThrough)} · 마지막 기록 ${hhmmOf(s.processedThrough)}`;
  const live = liveness(s);
  const livenessText =
    live.kind === 'live'
      ? `수집기 정상 · heartbeat ${hhmmOf(s.heartbeatAt)}`
      : `${live.label} · heartbeat ${hhmmOf(s.heartbeatAt)}`;

  const base = { reason: '', liveness: livenessText, progress, coverage, quality };

  /* 세션 시작 전 — 아직 아무것도 안 돈 것을 결손으로 그리지 않는다 */
  if (s.phase === 'PLANNED') {
    return { ...base, kind: 'waiting', label: '대기', tone: 'neutral', reason: '세션이 아직 시작되지 않았다(phase=PLANNED)' };
  }
  /* 종료 국면 — 최종 완결 상태를 함께 말한다.
   * ⚠️ 이 분기가 아래 결함·무증거·고착 검사보다 **먼저** 반환하므로, 결함을 안 보면
   * MISSING·INCOMPLETE 를 가진 마감 세션이 경고 없는 종료로 선다 — 같은 health 객체가
   * `quality.defects` 를 0 아닌 값으로 들고 있는데도. EOD 가 결손을 판정한 뒤라 오히려
   * 가장 확정적인 결함인데 배지만 깨끗해진다. 국면(종료)은 유지하고 **톤과 사유**로
   * 드러낸다 — 국면을 장애로 바꾸면 서버가 안 한 판정을 화면이 만든다. */
  if (s.phase === 'DRAINED' || s.phase === 'QC_RUNNING' || s.phase === 'FINALIZED') {
    /* ⚠️ **창 축만 세면 안 된다.** 이 분기는 아래 고착 job 경고보다도 먼저 반환하므로,
     * 창 결함은 없고 job 만 고착(lease 만료·DEAD)된 마감 세션이 깨끗한 종료로 선다 —
     * 같은 세션의 `quality.text` 와 `issues()` 는 그 job 을 그대로 드러내는데도. */
    const windowDefects = defects + w.overdueNoEvidence;
    const parts = [
      windowDefects > 0 ? `남은 결함 ${windowDefects}${noun}` : null,
      stuck > 0 ? `고착 job ${stuck}` : null,
      ledgerGap > 0 ? `원장 불일치 ${ledgerGap}${noun}` : null,
    ].filter(Boolean);
    return {
      ...base,
      kind: 'closed',
      label: '종료',
      tone: parts.length > 0 ? 'warn' : 'gated',
      reason:
        `phase=${s.phase} · 최종 연속 완결 ${hhmmOf(s.contiguousCompleteThrough)}` +
        (parts.length > 0 ? ` · ${parts.join(' · ')}` : ''),
    };
  }
  if (s.phase === 'FAILED') {
    return { ...base, kind: 'failure', label: '장애', tone: 'blocked', reason: '세션 phase=FAILED' };
  }
  /* 장애 — heartbeat 끊김 또는 기한 경과 무증거 */
  if (live.kind === 'broken') {
    return { ...base, kind: 'failure', label: '장애', tone: 'blocked', reason: live.basis };
  }
  if (w.overdueNoEvidence > 0) {
    return {
      ...base,
      kind: 'failure',
      label: '장애',
      tone: 'blocked',
      reason: `기한 경과 후 결과 증거 없음 ${w.overdueNoEvidence}${noun}`,
    };
  }
  /* 수집기가 살아 있어도 결함이 있으면 정상이 아니다 */
  if (defects > 0 || stuck > 0 || ledgerGap > 0) {
    return {
      ...base,
      kind: 'caution',
      label: '주의',
      tone: 'warn',
      reason:
        defects > 0
          ? `품질 결함 ${defects}${noun}`
          : stuck > 0
            ? `유효 lease 없는 claim·DEAD ${stuck}건`
            : `원장 불일치 — 기대 ${s.expectedWindowCount}${noun} 중 실재 행 ${materialized}`,
    };
  }
  if (live.kind === 'unknown') {
    return { ...base, kind: 'caution', label: '주의', tone: 'warn', reason: live.basis };
  }
  return {
    ...base,
    kind: 'normal',
    label: '정상',
    tone: 'active',
    reason: '창·품질·실행·큐 이상 없고 원장 행 수도 기대와 같다',
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
 * poll 레인 **공통** 조각 — 단위 명사만 '창'에서 'poll' 로 바뀌고 기전은 같은 것들.
 *
 * ⚠️ 부분 덮어쓰기 표는 **빠뜨린 키가 곧 기본(가격) 문구로 떨어진다.** 실제로 공시 표가
 * 결함 키를 안 덮어 "스텝이 유실을 판정한 창"이 공시 화면에 떴다. 레인마다 표를 따로
 * 적으면 그 누락이 레인 수만큼 는다 — 공통을 깔고 레인 고유 사실만 덮는다.
 */
const POLL_SEGMENTS: Partial<Record<SegmentKey, { label: string; meaning: string }>> = {
  missing: {
    label: 'MISSING (EOD 판정)',
    meaning: 'EOD reconciliation 이 결손으로 판정한 분 — 장중에는 매겨지지 않는다',
  },
  pending: {
    label: '미도래 · poll 중',
    meaning:
      '아직 기한이 안 온 분과 유효 lease 로 poll 중인 분이 한 통이다 — 이 응답은 둘을 가르지 않는다. 결함이 아니다.',
  },
  unmaterialized: {
    label: 'poll 행 없음',
    meaning: '예정 poll 수에는 있는데 원장에 행이 없다 — 어떤 집계에도 안 잡히는 materialize 결손 후보',
  },
  noEvidence: {
    label: '무증거',
    meaning:
      '판정: 기한 경과 후 결과 증거 없음. 원장 상태는 DUE 또는 유효 lease 없는 CLAIMED 다. 이 사실만으로 실행체 사망을 단정하지 않는다.',
  },
};

/**
 * 뉴스 poll 의 의미 덮어쓰기 — 같은 원장 컬럼이지만 사실이 다르다.
 * 근거: `data_pipeline/minute/commit.py` `commit_news_window`(격리→INVALID, truncated→
 * INCOMPLETE, 신규 0건→VALID_EMPTY) · `news_worker.py`(anchor 두 개와 따라잡기 예산).
 * 가격 문구를 그대로 두면 "거래가 없었다"가 뉴스 화면에 뜬다.
 */
const NEWS_SEGMENTS: Partial<Record<SegmentKey, { label: string; meaning: string }>> = {
  ...POLL_SEGMENTS,
  valid: {
    label: '신규 기사 관측',
    meaning: 'poll 이 돌았고 신규·정정 기사를 관측한 분이다.',
  },
  validEmpty: {
    label: '정상 · 신규 0건',
    meaning:
      'poll 이 돌았고 그 분에 신규 기사가 없었다는 **증거가 남은** 분 — 정상 poll 증거다. 뉴스는 이 조각이 다수인 게 정상이며, 무증거(결과 증거 없음)와 다른 사실이라 합쳐 세지 않는다.',
  },
  incomplete: {
    label: '잘린 poll · 따라잡기',
    meaning:
      'poll 이 직전 성공 anchor 에 닿기 전에 page 예산이 끝난 분(truncated)이다. 관측이 뒤처졌다는 뜻이고 다음 poll 이 더 깊은 예산으로 따라잡도록 예약된 상태다 — 성공으로 위장하지 않는다.',
  },
  invalid: {
    label: '격리 발생',
    meaning: '관측한 기사 중 격리된 건이 있어 무효로 커밋된 poll 이다.',
  },
  missing: {
    label: 'MISSING (EOD 판정)',
    meaning: 'EOD reconciliation 이 결손으로 판정한 분 — 장중에는 매겨지지 않는다',
  },
  noEvidence: {
    label: '무증거',
    meaning:
      '판정: 기한(window_end) 경과 후 결과 증거 없음. 원장 상태는 DUE 또는 유효 lease 없는 CLAIMED 다. **기사가 없었다는 뜻이 아니다** — 그건 신규 0건이다. 이 사실만으로 worker 사망을 단정하지 않는다.',
  },
  pending: {
    label: '미도래 · poll 중',
    meaning:
      '아직 기한이 안 온 분과 유효 lease 로 poll 중인 분이 한 통이다 — 이 응답은 둘을 가르지 않는다. 결함이 아니다.',
  },
  unmaterialized: {
    label: 'poll 행 없음',
    meaning: '예정 poll 수에는 있는데 원장에 행이 없다 — 어떤 집계에도 안 잡히는 materialize 결손 후보',
  },
};

/**
 * 어휘 밖 dataset 의 덮어쓰기 — `datasetKind` 가 'other' 로 두는 이유(모르는 것을 가격으로
 * 접으면 없는 의미가 붙는다)를 문구 층에서도 지킨다. 기본 조각 중 **`validEmpty` 하나만**
 * 데이터셋 고유 사실("그 분에 거래가 없었다")을 단정한다 — 나머지는 원장 컬럼 그대로라
 * 어느 데이터셋에나 참이다. 새 분 데이터셋이 붙는 날(inav·업종지수 등) 이 자리가 그 축의
 * 사실을 모른 채 가격 문구를 붙이지 않게 한다.
 */
const OTHER_SEGMENTS: Partial<Record<SegmentKey, { label: string; meaning: string }>> = {
  validEmpty: {
    label: '정상 · 빈 데이터',
    meaning:
      '돌았고 그 분의 결과가 비었다는 **증거가 남은** 창 — 실행 증거가 있으므로 정상 귀결이다. 무엇이 비었는지는 이 데이터셋의 의미에 달렸고 여기서 단정하지 않는다.',
  },
};

/**
 * 공시 poll 의 의미 덮어쓰기 — 뉴스와 같은 poll 축이지만 사실은 "기사"가 아니라 "공시"다.
 * 뉴스 표를 재사용하면 공시 화면에 "신규 기사"가 뜬다.
 */
const DISCLOSURE_SEGMENTS: Partial<Record<SegmentKey, { label: string; meaning: string }>> = {
  ...POLL_SEGMENTS,
  valid: { label: '신규 공시 관측', meaning: 'poll 이 돌았고 신규 공시를 관측한 분이다.' },
  /* 공시의 불완전은 anchor 따라잡기가 아니다 — 증분 커서가 없어 그 기전 자체가 없다.
   * `commit_disclosure_window` 는 하위 스텝 부분 실패를 INCOMPLETE 로 커밋한다. */
  incomplete: {
    label: '부분 실패 poll',
    meaning:
      'poll 은 돌았으나 하위 스텝이 부분 실패한 분(INCOMPLETE) — 성공으로 위장하지 않는다.',
  },
  invalid: {
    label: '무효 커밋',
    meaning: '실패 unit 이 있어 무효로 커밋된 poll 이다.',
  },
  validEmpty: {
    label: '정상 · 신규 0건',
    meaning:
      'poll 이 돌았고 그 분에 신규 공시가 없었다는 **증거가 남은** 분 — 정상 poll 증거다. 무증거(결과 증거 없음)와 다른 사실이라 합쳐 세지 않는다.',
  },
  pending: {
    label: '미도래 · poll 중',
    meaning:
      '아직 기한이 안 온 분과 유효 lease 로 poll 중인 분이 한 통이다 — 이 응답은 둘을 가르지 않는다. 결함이 아니다.',
  },
  unmaterialized: {
    label: 'poll 행 없음',
    meaning: '예정 poll 수에는 있는데 원장에 행이 없다 — 어떤 집계에도 안 잡히는 materialize 결손 후보',
  },
};

/** 어휘가 늘면 `Record` 가 여기서 컴파일을 막는다 — 새 레인이 조용히 가격 문구로 떨어지지 않게 */
const SEGMENT_OVERRIDES: Record<
  DatasetKind,
  Partial<Record<SegmentKey, { label: string; meaning: string }>> | undefined
> = {
  price: undefined,
  news: NEWS_SEGMENTS,
  disclosure: DISCLOSURE_SEGMENTS,
  other: OTHER_SEGMENTS,
};

/**
 * 분별 전체 타임라인 대신 쓰는 구간 요약. 각 조각은 응답이 실제로 준 카운트이고,
 * 합은 expectedWindowCount 다 — 없는 분모를 만들지 않는다.
 *
 * `pending` 은 "미도래"와 "수집 중"을 **응답이 못 가르는** 한 통이다. 이 둘을 갈라 진행률을
 * 그리려면 서버가 도래 여부를 내려줘야 한다(minuteViewApiGaps 참고).
 *
 * 라벨·의미는 dataset 이 정한다 — 카운트 축은 같아도 사실이 다르기 때문이다(NEWS_SEGMENTS).
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
        '돌았는데 그 분에 거래가 없었다는 **증거가 남은** 창 — 실행 증거가 있으므로 정상 귀결이다. 무증거(결과 증거 없음)와 다른 사실이라 합쳐 세지 않는다.',
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
        '판정: 기한(window_end) 경과 후 결과 증거 없음. 원장 상태는 DUE 또는 유효 lease 없는 CLAIMED 다. 서버(DB 시계) 판정이며, 이 사실만으로 미실행·실행체 사망을 확정하지 않는다.',
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
  const override = SEGMENT_OVERRIDES[datasetKind(s.dataset)];
  return all
    .filter((seg) => seg.count > 0)
    .map((seg) => (override?.[seg.key] ? { ...seg, ...override[seg.key]! } : seg));
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
  /* 단위·명사는 dataset 이 정한다 — 뉴스·공시 화면에 "창"·"거래"가 나오면 없는 의미가 붙는다.
   * ⚠️ `poll` 과 `news` 는 다른 축이다: 단위 명사는 poll 레인 공통이지만, **따라잡기(anchor
   * 미도달)는 뉴스 worker 고유**다 — 공시는 증분 커서가 없어 매 tick 이 날짜창 전체를 다시
   * 읽으므로 "뒤처진 anchor" 라는 사실 자체가 없다. 한 변수로 접으면 없는 기전이 붙는다. */
  const kind = datasetKind(s.dataset);
  const poll = isPollLane(kind);
  const news = kind === 'news';
  const unit = windowUnit(kind);
  const noun = poll ? 'poll' : '창';

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
      title: `무증거 ${noun} — 기한이 지났는데 결과가 없다`,
      short: '무증거',
      count: w.overdueNoEvidence,
      unit,
      tone: 'blocked',
      range: rangeOf(noEvidenceGaps),
      detail: [
        '판정 — 기한(window_end) 경과 후 결과 증거 없음',
        '원장 상태 — DUE 또는 유효 lease 없는 CLAIMED (서버 DB 시계 판정)',
        poll
          ? '구분 — 신규 0건(VALID_EMPTY)은 poll 실행 증거가 있어 정상 귀결로 따로 집계'
          : '구분 — 빈 데이터(VALID_EMPTY)는 실행 증거가 있어 정상 귀결로 따로 집계',
        '다음 확인 — 세션 heartbeat · lease · 관련 job',
      ].join(' · '),
    });
  }

  const qualityGaps = s.gaps.filter((g) => !g.noEvidence);
  const quality = qualityDefectCount(s);
  if (quality > 0) {
    out.push({
      key: 'quality',
      title: news
        ? '품질 결함 poll — 잘린 poll(따라잡기) · 격리 · MISSING'
        : poll
          ? '품질 결함 poll — 부분 실패 · 격리 · MISSING'
          : '품질 결함 창 — 불완전 · 무효 · MISSING',
      short: '품질 결함',
      count: quality,
      unit,
      tone: 'warn',
      range: rangeOf(qualityGaps),
      detail: news
        ? 'anchor 에 못 닿고 잘린 poll(관측이 뒤처졌다), 격리분이 있어 무효로 커밋된 poll, EOD 가 결손으로 판정한 분이다.'
        : poll
          ? '하위 스텝이 부분 실패한 poll(INCOMPLETE — `commit_disclosure_window` 가 그렇게 커밋한다), 격리분이 있어 무효로 커밋된 poll, EOD 가 결손으로 판정한 분이다.'
          : '결과는 남았지만 정상이 아닌 창과 EOD QC 가 결손으로 판정한 창이다.',
    });
  }

  const materialized = materializedCount(s);
  if (materialized !== s.expectedWindowCount) {
    out.push({
      key: 'ledgerMismatch',
      title: `원장 불일치 — ${poll ? '예정 poll' : '기대 창'} 수와 실재 행 수가 다르다`,
      short: '원장 불일치',
      count: Math.abs(s.expectedWindowCount - materialized),
      unit,
      tone: 'blocked',
      range: null,
      detail: `${poll ? '예정' : '기대'} ${s.expectedWindowCount} vs 실재 ${materialized}. 행이 없는 ${noun}은 무증거를 포함한 어떤 집계에도 안 잡히므로 위 숫자들을 그대로 믿으면 안 된다.`,
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
        '판정 — status=CLAIMED 인데 유효한 lease 가 없다(만료 또는 NULL, writer 의 회수 조건과 같은 집합). 재청구 대상인 고착 후보이며 **consumer 사망 확정이 아니다**. "처리 중"에 뭉개면 영원히 경고가 없다.',
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
export interface ApiGap {
  need: string;
  why: string;
}

/**
 * 뉴스 레인에만 해당하는 부채 — 가격 탭에 섞어 놓으면 어느 데이터셋의 한계인지 흐려진다.
 * 근거: `news_poll_anchor` 마이그레이션 · `news_worker.py`(차단 쿨다운·claim 반납) ·
 * `JdbcMinuteStatusRepository.NEWS_JOBS_SQL`(날짜 축 집계).
 */
export const NEWS_API_GAPS: ApiGap[] = [
  {
    need: 'poll anchor 의 따라잡기 상태 (success_poll_at vs head_poll_at)',
    why: '원장에는 "직전 성공 anchor 에 못 닿았다"는 lag 예약이 있는데 응답에 없다 — 화면은 잘린 poll 카운트로 뒤처짐을 간접 관측할 뿐 "지금도 뒤처져 있는가"에 답하지 못한다.',
  },
  {
    need: '벤더 차단 쿨다운 상태 (blocked_until · 차단 사유)',
    why: '차단되면 Worker 가 claim 을 즉시 반납해 DUE 로 돌아간다 — 기한이 지나면 "무증거"와 **같은 모양**이 된다. 억제 중인 것과 안 돈 것을 응답으로 가를 수 없다.',
  },
  {
    need: '뉴스 추출 job 의 세션·소스 축',
    why: 'news_extraction_job 은 세션 연결 컬럼이 없어 생성 시각(KST) 날짜로만 집계된다 — 장중 세션이 만든 job 과 백필 생산자의 job 이 한 통이라 이 숫자를 세션 판정에 쓸 수 없다.',
  },
];

export const MINUTE_API_GAPS: ApiGap[] = [
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
