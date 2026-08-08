/* 규칙 엔진 화면들의 공통 조각 (ALPHA-738).
 *
 * 규칙 평가는 `useConsoleEvaluation()` 한 곳에서만 돈다 — 여섯 화면이 같은 결과를 읽는다.
 * 모듈 상수가 아니라 훅인 이유는 실시간(1분) 세션 축이 스냅샷이 아니라 API 응답이라서다.
 * 스타일은 ui-kit 토큰·프리미티브만 쓴다(자체 팔레트 금지). 색은 상태 신호 전용이고,
 * 출처·목데이터 같은 메타는 무채색 chip 으로 낸다.
 */
import { useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { BadgeTone } from 'ui-kit';
import { evaluate, runbookOf as lookupRunbook } from '../../rules/evaluate';
import { InfoPopover } from '../_shared/InfoPopover';
import type { AxisFetch } from './notRun';
import type {
  Evaluation,
  Facts,
  Incident,
  MinuteFacts,
  RunbookEntry,
  Severity,
  Violation,
} from '../../rules/types';
import factsJson from '../../rules/facts-snapshot.json';
import type { MinuteStatus } from '../../domains/sources';
import { useMinuteStatus } from '../../domains/sources/hooks';
import { datasetKind } from '../../domains/sources/minuteView';

/* 스냅샷에는 규칙이 읽지 않는 표시 전용 축도 들어 있다.
 *
 * ⚠️ 퍼널 단계의 주의사항은 **완성된 문장이 아니라 구조화된 필드**다 — 화면이 그 값으로
 * 배지·설명을 결정적으로 만든다. 문장을 데이터에 박아 두면 표 셀이 문단이 되고, 값이 바뀌어도
 * 문장은 그대로 남는다(예: 상한 도달 런이 0인데 "상한 절단값" 문구가 남는 일). */
interface FunnelStep {
  stage: string;
  value: number;
  unit: string;
  /** 창 겹침이 값에 포함되는가 */
  includesWindowOverlap?: boolean;
  /** 중복이 값에 포함되는가 */
  includesDuplicates?: boolean;
  /** 런당 수집 상한 = maxPages × pageSize */
  maxPages?: number;
  pageSize?: number;
  totalRuns?: number;
  /** 상한에 도달한 런 수 — 0 보다 크면 "수집 상한 도달" */
  runsAtLimit?: number;
  dedupKey?: string;
  /** 앞 단계와 시각 축이 다르면 값이 어긋나는 게 정상이다 */
  timestampAxis?: string;
  /** 유니버스 필터가 걸리는 단계인가 */
  universeFilter?: boolean;
  /** 실질 탈락 단계인가 */
  dropStage?: boolean;
  /** 부재 사유 계측이 있는가. false = 사유가 한 통이라 원인을 못 가른다 */
  missingReasonInstrumentation?: boolean;
  /** 구조화할 수 없는 짧은 보충만 남긴다 */
  note?: string;
}
interface DeliveryFacts {
  coverage_0803: { published_without_new_delivery: number; new_delivery_now_nonpublished: number };
  integrity_0803: { delivery_rows: number };
}
export type ConsoleFacts = Facts & { news_funnel: FunnelStep[]; delivery: DeliveryFacts };

export const F = factsJson as unknown as ConsoleFacts;


export const fmt = (n: unknown): string =>
  n == null ? '—' : typeof n === 'number' ? n.toLocaleString('ko-KR') : String(n);

export const kst = (iso?: string | null): string =>
  iso
    ? new Intl.DateTimeFormat('ko-KR', {
        timeZone: 'Asia/Seoul',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(iso))
    : '—';

/** 심각도 → 배지 톤. P0=차단, P1=주의, P2=중립 (ui-kit 시맨틱 그대로) */
export const SEV_TONE: Record<Severity, BadgeTone> = { P0: 'blocked', P1: 'warn', P2: 'neutral' };

/** 스냅샷 위의 런북 조회 — 키 공식은 규칙 층(`rules/evaluate`)이 한 벌만 갖는다 */
export function runbookOf(v: Violation): RunbookEntry | undefined {
  return lookupRunbook(F, v);
}

/**
 * 목록의 ⓘ — **"이 줄이 왜 사건인가"** 하나만 답한다.
 *
 * 예전에는 규칙·층·심각도·대상·목록·마지막 정상·연쇄 전문·조치를 다 담아 사건 상세를 거의
 * 복제했다. 목록에서 그만큼 읽을 사람은 없고, 읽을 거면 상세로 가는 게 맞다. 그리고 여기 담긴
 * 값의 다수는 이미 같은 행의 열이 말한다(규칙·층·수치·연쇄).
 *
 * 여기서 뺀 것이 사라지지는 않는다 — 대상 목록·마지막 정상·귀결률·조치 명령은 사건 상세가 답한다.
 */
export function violationTip(v: Violation, I?: Incident): string {
  const lines = [`왜: ${v.why}`, `근거: ${v.evidence}`];
  if (I && I.members.length) {
    lines.push('', `인과로 묶인 위반 ${I.members.length}건 — 무엇이 묶였는지는 사건 상세가 답합니다`);
  }
  if (v.mock) lines.push('', `MOCK — 이 규칙은 목데이터에 의존합니다. 실제 계측: ${v.dep ?? '—'}`);
  return lines.join('\n');
}

/* ── 담당 도메인 ──
 * 사건이 어느 팀·화면 소관인지는 위반의 drill 축으로 **결정적으로** 정해진다.
 * 새 분류기를 만들지 않는다 — 모르는 축은 규칙의 layer 로, 그것도 없으면 '기타'다. */
export const DOMAIN_OF_DRILL: Record<string, string> = {
  run: '파이프라인',
  dataset: '파이프라인',
  trend: '파이프라인',
  chain: '설명 생성',
  delivery: '테넌트 전달',
};

export function domainOf(v: Violation): string {
  return DOMAIN_OF_DRILL[v.drill[0]] ?? v.layer ?? '기타';
}

/**
 * 이 콘솔이 운영을 책임지는 범위 밖의 도메인. **룰 엔진은 그대로 돌리고 표시에서만 가른다** —
 * 규칙을 지우면 "안 봤다"와 "봤는데 소관이 아니다"가 같은 모양이 된다.
 *
 * 파이프라인 운영 범위: 파이프라인 실행 · 일배치/실시간 수집 · 데이터 완전성 · 분석 실행 ·
 * 분석 결과 생성. 테넌트별 전달·발번·온프렘 수신·최종 게시·MTS 노출은 그 밖이다(ADR-0026).
 */
export const OUT_OF_PIPELINE_DOMAINS = new Set(['테넌트 전달']);

/**
 * API DTO → 규칙 사실. **규칙은 화면 도메인을 모른다** — 맞추는 일은 여기서 한 번만 한다.
 *
 * `deadJobs` 를 어디서 가져오는지가 이 어댑터의 유일한 판단이다: 뉴스 job 은 세션 연결 컬럼이
 * 없어 날짜 축 집계(`newsJobs`)이고 가격은 세션에 붙어 있다. 규칙은 그 사정을 모른 채
 * "이 데이터셋의 DEAD 수"만 받는다.
 */
export function minuteFacts(s: MinuteStatus): MinuteFacts {
  return {
    date: s.date,
    sessions: s.sessions.map((x) => ({
      dataset: x.dataset,
      /* 세션 identity 는 `(dataset, sourceGroup, date)` 다 — 여기서 버리면 규칙이 벤더가 다른
       * 두 세션을 한 대상으로 보고, 사건 식별자가 겹쳐 딥링크가 다른 세션을 연다 */
      sourceGroup: x.sourceGroup,
      phase: x.phase,
      leaseExpired: x.leaseExpired,
      overdueNoEvidence: x.windows.overdueNoEvidence,
      deadJobs: (datasetKind(x.dataset) === 'news' ? s.newsJobs : x.priceJobs).dead,
    })),
  };
}

export interface ConsoleEvaluation extends Evaluation {
  /** 파이프라인·분석 소관 사건만. 전달 사건을 파이프라인 P0·심각도 합계에 섞지 않는다 */
  pipeline: Incident[];
  /** 범위 밖 사건 — 숨기지 않고 "여기 소관이 아니다"로 세어 보인다 */
  outOfScope: Incident[];
  /** 실시간 축이 실렸는가. false 면 R17~R19 는 evaluated:false(못 돌았다)다 */
  minuteLoaded: boolean;
  /**
   * 그 실시간 축을 **왜** 못 실었는가. `못 돎` 문장의 뜻이 여기 달려 있다 —
   * 응답 대기·조회 실패를 "계측이 없다"로 그리면 운영자가 **API 장애를 미배선으로 읽는다**.
   */
  axisFetch: AxisFetch;
  /**
   * 이 평가에 실제로 쓴 사실. 조사 경로(`investigate`)도 **같은 사실**을 읽어야 한다 —
   * 정적 스냅샷으로 만들면 실시간 사건의 드릴다운이 세션 응답의 날짜가 아니라
   * 배치 거래일(meta.today)을 가리킨다(그 날짜엔 세션이 없다).
   */
  facts: ConsoleFacts;
}

/**
 * 콘솔의 사건 평가 — **유일한 출처**다.
 *
 * 예전에는 모듈 상수(`INCIDENTS`)였다. 그러면 실시간 레인을 영원히 못 본다: 세션 원장은
 * 스냅샷이 아니라 API 응답이라 import 시점에 없기 때문이다. `evaluate` 가 순수 함수라
 * 응답이 도착하면 사실을 합쳐 다시 돌리면 된다 — 판정을 화면에 쓰지 않고 규칙에 쓴다.
 *
 * ⚠️ 사건 목록을 두 벌로 두지 않는다. 소비자가 전부 이 훅을 쓰는 이유다 — 한쪽만 실시간을
 * 보면 목록엔 있는데 상세는 못 여는 사건이 생긴다.
 */
export function useConsoleEvaluation(): ConsoleEvaluation {
  /* `isError` 를 같이 꺼내는 이유: 실시간 축 부재의 뜻이 셋이다 — 도착 전 · 조회 실패 ·
   * (응답은 왔는데) 축이 없다. `data` 만 보면 셋이 한 문장으로 뭉쳐, 응답 대기와 API 장애가
   * 화면에서 "계측 없음"과 구분되지 않는다. */
  const { data, isError } = useMinuteStatus();
  return useMemo(() => {
    const facts: ConsoleFacts = data ? { ...F, minute: minuteFacts(data) } : F;
    const ev = evaluate(facts);
    return {
      ...ev,
      pipeline: ev.incidents.filter((i) => !OUT_OF_PIPELINE_DOMAINS.has(domainOf(i.root))),
      outOfScope: ev.incidents.filter((i) => OUT_OF_PIPELINE_DOMAINS.has(domainOf(i.root))),
      minuteLoaded: data != null,
      axisFetch: data != null ? 'loaded' : isError ? 'error' : 'pending',
      facts,
    };
  }, [data, isError]);
}

/* 판정을 못 한 규칙을 묻는 헬퍼는 `./notRun` 에 있다 — **JSX 가 없는 모듈이라야
 * `node --test` 가 import 한다.** 여기 두는 동안은 그 판단들의 변이가 하나도 안 잡혔다. */

/* `DRILL_ROUTE`·`drillHref` 를 지웠다 (ALPHA-738 단계 3). 소비자가 0이었고, 살아나면
 * `run` 축 위반을 `/ops/runs?focus=…` 로 보내 실행 상세 링크 규약을 우회한다
 * (investigation.ts 의 `RUN_DETAIL_UNAVAILABLE`). 조사 경로는 `investigate()` 하나다. */

/** `?focus=<id>` 로 들어오면 그 행으로 스크롤하고 잠깐 강조한다 (L3) */
export function useFocusRow(): string | null {
  const [params] = useSearchParams();
  const focus = params.get('focus');
  useEffect(() => {
    if (!focus) return;
    const n = document.getElementById(focus);
    if (!n) return;
    n.scrollIntoView({ behavior: 'smooth', block: 'center' });
    n.classList.add('ops-flash');
    const t = setTimeout(() => n.classList.remove('ops-flash'), 1800);
    return () => clearTimeout(t);
  }, [focus]);
  return focus;
}

/** 출처 배지 — 어느 표면이 준 숫자인가. 무채색(상태 신호가 아니다) */
export function SourceChip({ source }: { source: string }) {
  const LABEL: Record<string, string> = {
    DB_LEDGER: 'DB 원장',
    S3_LOG: 'S3 로그',
    AWS_CONTROL: 'AWS 제어면',
    'AWS_CONTROL+DB_LEDGER': 'AWS 제어면 · DB 원장',
    CODE: 'CODE 설정',
    SEED: 'SEED',
    MOCK: 'MOCK',
  };
  return (
    <span
      className="chip"
      title={
        source === 'MOCK'
          ? '목데이터 — 계측이 없어 규칙이 돌 수 있는 최소 사실로 채웠다. 실측과 모순되지 않게 잡았지만 실측은 아니다.'
          : source === 'SEED'
            ? '로컬 compose 시드 잔재 — 운영 데이터가 아니다.'
            : `출처: ${LABEL[source] ?? source}`
      }
    >
      {LABEL[source] ?? source}
    </span>
  );
}

/** 부재 4구분 — 0(실측 0) · —(집계 없음) · 관측 불가 · 계측 없음. 셋을 0으로 그리면 결함이다. */
export function Absent({ kind }: { kind: 'none' | 'blind' | 'uninstrumented' }) {
  const T = {
    none: { text: '—', tip: '집계 없음 — 이 축을 세지 않는다(값이 0이라는 뜻이 아니다).' },
    blind: { text: '관측 불가', tip: '접근 채널이 없어 볼 수 없다 — 0이 아니다.' },
    uninstrumented: { text: '계측 없음', tip: '기록을 남기지 않는다 — 0으로 그리면 거짓이다.' },
  }[kind];
  return (
    <span className="t-xs" style={{ color: 'var(--fg-4)' }} title={T.tip}>
      {T.text}
    </span>
  );
}

/** ⓘ — L2 정보 공개 손잡이. 클릭으로 열리는 팝오버다(hover 전용 title 은 키보드·터치에서 없는 것과 같다) */
export function Info({ tip, label }: { tip: string; label: string }) {
  return <InfoPopover text={tip} label={label} />;
}

/** 화면 상단 한 줄 — 이 화면이 답하는 질문 + 스냅샷 기준 시각 */
export function AxisHeader({ question, note }: { question: string; note?: string }) {
  return (
    <div className="card card-pad">
      <p className="t-sm m-0">{question}</p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
        기준 DB {kst(F.meta.db)} · AWS/S3 {kst(F.meta.aws)} · 거래일 {F.meta.today}
        {note ? ` · ${note}` : ''}
      </p>
    </div>
  );
}
