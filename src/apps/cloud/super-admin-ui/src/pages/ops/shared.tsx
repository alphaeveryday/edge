/* 규칙 엔진 화면들의 공통 조각 (ALPHA-738).
 *
 * 규칙 평가는 `useConsoleEvaluation()` 한 곳에서만 돈다 — 여섯 화면이 같은 결과를 읽는다.
 * 모듈 상수가 아니라 훅인 이유는 **두 축 다 API 응답**이기 때문이다: 배치 사실은
 * `/console/facts`, 실시간 세션은 `/sources/minute`. 앱 번들 안 정적 스냅샷을 읽던 동안은
 * 어느 쪽도 import 시점에 없어서 화면이 실 원장을 영영 못 봤다(ALPHA-738 D 에서 뒤집었다).
 * 스타일은 ui-kit 토큰·프리미티브만 쓴다(자체 팔레트 금지). 색은 상태 신호 전용이고,
 * 출처·목데이터 같은 메타는 무채색 chip 으로 낸다.
 */
import { useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageSkeleton } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { evaluate } from '../../rules/evaluate';
import { InfoPopover } from '../_shared/InfoPopover';
import { LoadError } from '../_shared/LoadError';
import type { AxisFetch } from './notRun';
import { awsObservation, axisOf, factsAxis, minuteFacts, parseFacts } from './consoleFacts';
import type { Evaluation, Facts, Incident, Severity, Violation } from '../../rules/types';
import { useConsoleFacts } from '../../domains/console/hooks';
import { useMinuteStatus } from '../../domains/sources/hooks';

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
 * 배치 사실 축이 아직 안 섰다 — **화면을 그리지 않는다**.
 *
 * 사실이 없는데 표를 그리면 빈 표가 되고, 빈 표는 "위반 0건 · 런 0건"으로 읽힌다. 그게 이
 * 콘솔이 없애려는 칸 혼동이라, 사실 축은 규칙별 `못 돎` 이 아니라 **화면 단위 조회 실패**다
 * (계약 §「B 의 선행 조건」). 실시간 축(`axisFetch`)과 다른 이유: 그쪽은 19규칙 중 3개만
 * 읽는 보조 축이라 없어도 나머지 판정이 선다.
 */
export interface ConsoleFactsPending {
  ready: false;
  fetch: 'pending' | 'error';
  error: unknown;
  /** 검증 경계가 응답을 거부한 사유. 네트워크 실패면 `null` — 두 실패는 다른 사실이다 */
  reason: string | null;
}
export interface ConsoleFactsReady {
  ready: true;
  /** `stale` = 직전 응답은 검증을 통과했는데 마지막 조회가 실패했다(판정이 낡았다) */
  fetch: 'loaded' | 'stale';
  facts: Facts;
}
export type ConsoleFactsQuery = ConsoleFactsPending | ConsoleFactsReady;

/**
 * 배치 사실 — **검증 경계를 통과한 것만** 화면에 준다(`parseFacts`).
 *
 * 규칙 평가가 필요 없는 화면(데이터셋·전달·체인·추이)이 엔진을 돌리지 않고 사실만 읽는 자리다.
 * `useConsoleEvaluation` 도 이 훅 위에 선다 — 사실 출처가 둘이면 한 화면에서 다른 시각의
 * 사실이 조립된다.
 */
export function useConsoleFactsQuery(): ConsoleFactsQuery {
  const { data, isError, error } = useConsoleFacts();
  return useMemo(() => {
    const parsed = data !== undefined ? parseFacts(data) : null;
    const fetch = factsAxis(parsed, isError);
    if (parsed?.ok && (fetch === 'loaded' || fetch === 'stale')) {
      return { ready: true, fetch, facts: parsed.facts };
    }
    return {
      ready: false,
      fetch: fetch === 'pending' ? 'pending' : 'error',
      error,
      reason: parsed && !parsed.ok ? parsed.reason : null,
    };
  }, [data, isError, error]);
}

/** 사실 축이 서기 전의 화면. 다른 화면들과 같은 관용구다(`isError` → LoadError, `isPending` → 스켈레톤) */
export function ConsoleGate({ q }: { q: ConsoleFactsPending }) {
  if (q.fetch === 'pending') return <PageSkeleton rows={5} />;
  return (
    <div className="flex flex-col gap-4">
      <LoadError error={q.error} />
      {q.reason && (
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>응답이 계약과 다릅니다</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {q.reason} — 일부 행만 골라 그리지 않습니다. 망가진 응답은 부분적으로도 믿을 수 없고,
            그때 그린 표는 <b>실측처럼 보이는 거짓</b>이 됩니다.
          </p>
        </div>
      )}
    </div>
  );
}

export type ConsoleEvaluation =
  | ConsoleFactsPending
  | (ConsoleFactsReady &
      Evaluation & {
        /** 파이프라인·분석 소관 사건만. 전달 사건을 파이프라인 P0·심각도 합계에 섞지 않는다 */
        pipeline: Incident[];
        /** 범위 밖 사건 — 숨기지 않고 "여기 소관이 아니다"로 세어 보인다 */
        outOfScope: Incident[];
        /**
         * 그 **실시간** 축을 왜 못 실었는가. `못 돎` 문장의 뜻이 여기 달려 있다 —
         * 응답 대기·조회 실패를 "계측이 없다"로 그리면 운영자가 API 장애를 미배선으로 읽는다.
         */
        axisFetch: AxisFetch;
      });

/**
 * 콘솔의 사건 평가 — **유일한 출처**다.
 *
 * 예전에는 모듈 상수(`INCIDENTS`)였고, 그 뒤로도 배치 축은 앱 번들 안의 정적 스냅샷이었다.
 * 그러면 실 원장을 영원히 못 본다: 사실은 import 시점에 없기 때문이다. `evaluate` 가 순수
 * 함수라 응답이 도착하면 사실을 합쳐 다시 돌리면 된다 — 판정을 화면에 쓰지 않고 규칙에 쓴다.
 *
 * ⚠️ 사건 목록을 두 벌로 두지 않는다. 소비자가 전부 이 훅을 쓰는 이유다 — 한쪽만 실시간을
 * 보면 목록엔 있는데 상세는 못 여는 사건이 생긴다.
 */
export function useConsoleEvaluation(): ConsoleEvaluation {
  const q = useConsoleFactsQuery();
  /* `isError` 를 같이 꺼내는 이유: 실시간 축 부재의 뜻이 셋이다 — 도착 전 · 조회 실패 ·
   * (응답은 왔는데) 축이 없다. `data` 만 보면 셋이 한 문장으로 뭉쳐, 응답 대기와 API 장애가
   * 화면에서 "계측 없음"과 구분되지 않는다. */
  const { data, isError } = useMinuteStatus();
  return useMemo(() => {
    if (!q.ready) return q;
    const facts: Facts = data ? { ...q.facts, minute: minuteFacts(data) } : q.facts;
    const ev = evaluate(facts);
    return {
      ...q,
      ...ev,
      facts,
      pipeline: ev.incidents.filter((i) => !OUT_OF_PIPELINE_DOMAINS.has(domainOf(i.root))),
      outOfScope: ev.incidents.filter((i) => OUT_OF_PIPELINE_DOMAINS.has(domainOf(i.root))),
      axisFetch: axisOf(data != null, isError),
    };
  }, [q, data, isError]);
}

/* 판정을 못 한 규칙을 묻는 헬퍼는 `./notRun` 에 있다 — **JSX 가 없는 모듈이라야
 * `node --test` 가 import 한다.** 여기 두는 동안은 그 판단들의 변이가 하나도 안 잡혔다. */

/* `DRILL_ROUTE`·`drillHref` 를 지웠다 (ALPHA-738 단계 3). 소비자가 0이었고, 살아나면
 * `run` 축 위반을 `/ops/runs?focus=…` 로 보내 실행 상세 링크 규약을 우회한다
 * (investigation.ts 의 `RUN_DETAIL_UNAVAILABLE`). 조사 경로는 `investigate()` 하나다. */

/**
 * `?focus=<id>` 로 들어오면 그 행으로 스크롤하고 잠깐 강조한다 (L3).
 *
 * 🔴 **`ready` 는 필수다.** 화면이 비동기가 되면서(ALPHA-738 D) 첫 렌더는 `ConsoleGate` 의
 * 스켈레톤이라 대상 행이 아직 DOM 에 없다. effect 가 `[focus]` 에만 걸려 있으면 그때 한 번
 * 헛돌고, 사실이 도착해 행이 mount 돼도 `focus` 는 안 바뀌므로 **다시 돌지 않는다** —
 * 사건에서 넘어온 딥링크가 조용히 스크롤·강조를 잃는다(봇이 잡았다).
 *
 * 기본값을 두지 않는 이유는 `notRunReason` 의 `fetch` 와 같다: 하필 그 기본값(`true`)이
 * 예전 동작이라, 인자를 빠뜨린 다음 소비자가 **조용히 이 버그로 회귀**한다. 필수로 두면 tsc 가
 * 잡는다.
 *
 * @param ready 대상 행이 그려질 수 있는 상태인가(= 게이트를 통과했는가)
 */
export function useFocusRow(ready: boolean): string | null {
  const [params] = useSearchParams();
  const focus = params.get('focus');
  useEffect(() => {
    if (!focus || !ready) return;
    const n = document.getElementById(focus);
    if (!n) return;
    n.scrollIntoView({ behavior: 'smooth', block: 'center' });
    n.classList.add('ops-flash');
    const t = setTimeout(() => n.classList.remove('ops-flash'), 1800);
    return () => clearTimeout(t);
  }, [focus, ready]);
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
    SNAPSHOT: '스냅샷',
    MOCK: 'MOCK',
  };
  return (
    <span
      className="chip"
      title={
        source === 'MOCK'
          ? '목데이터 — 계측이 없어 규칙이 돌 수 있는 최소 사실로 채웠다. 실측과 모순되지 않게 잡았지만 실측은 아니다.'
          : source === 'SNAPSHOT'
            ? '앱에 동봉된 스냅샷 — 한때 실제로 관측한 값이지만 이번 응답이 주지 않는다. 지어낸 값(MOCK)과 다르고, 오늘 값도 아니다.'
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

/**
 * AWS 제어면 관측 시각 — **부재가 두 형상이고 뜻이 다르다**(계약 §부재를 싣는 규약).
 *
 * - 키 자체가 없다 → **미배선**(C 축). 조회를 시도조차 안 했다.
 * - 키는 있는데 `null` → **조회했는데 못 봤다**(예: `sqs:GetQueueAttributes AccessDenied`).
 *   그때 서버는 `awsUnavailable` 사유를 함께 보낸다.
 *
 * `kst(undefined)` 는 둘 다 `—`(집계 없음)로 접는다 — 그러면 실제 제어면 장애가 "계측이 없구나"로
 * 읽힌다(리뷰가 두 라운드 연속 잡은 방향). 세 자리(`AxisHeader`·`IncidentsPage`·
 * `IncidentDetailPage`)가 같은 판단을 하도록 **여기 한 벌**로 둔다.
 */
export function AwsObservedAt({
  meta,
  format = kst,
}: {
  meta: Facts['meta'];
  format?: (iso: string) => string;
}) {
  /* 판단은 JSX 밖(`consoleFacts.awsObservation`)에 둔다 — 여기 두면 `node --test` 가 import 을
   * 못 해 변이가 하나도 안 잡힌다(이 파일에서 두 번 겪은 일이다). 여기는 그리기만 한다. */
  const o = awsObservation(meta);
  if (o === 'uninstrumented') return <Absent kind="uninstrumented" />;
  if (o === 'blind') return <Absent kind="blind" />;
  return <>{format(o.at)}</>;
}

/**
 * 화면 상단 한 줄 — 이 화면이 답하는 질문 + 그 사실의 기준 시각.
 *
 * `q` 를 통째로 받는 이유: 기준 시각과 **그 시각이 지금 것인가**는 같이 읽어야 한다.
 * `stale`(마지막 조회 실패)을 안 밝히면 1분마다 도는 화면에서 낡은 판정이 현재로 읽힌다.
 */
export function AxisHeader({
  q,
  question,
  note,
}: {
  q: ConsoleFactsReady;
  question: string;
  note?: string;
}) {
  return (
    <div className="card card-pad">
      <p className="t-sm m-0">{question}</p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
        {/* ⚠️ `meta.today` 는 **거래일이 아니다** — "원장이 아는 가장 최근 날"이라 휴장일·
            계획만 있던 날도 온다(계약 §무엇이 실제로 나가는가). `거래일` 이라 쓰면 운영자가
            조회 축을 거래일 달력으로 읽는다. */}
        기준 DB {kst(q.facts.meta.db)} · AWS/S3 <AwsObservedAt meta={q.facts.meta} /> · 조회일{' '}
        {q.facts.meta.today}
        {note ? ` · ${note}` : ''}
        {q.fetch === 'stale' && (
          <b style={{ color: 'var(--warn)' }}>
            {' · '}마지막 조회 실패 — 이 값은 직전 응답입니다
          </b>
        )}
      </p>
    </div>
  );
}

