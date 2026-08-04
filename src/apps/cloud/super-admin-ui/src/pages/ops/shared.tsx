/* 규칙 엔진 화면들의 공통 조각 (ALPHA-738).
 *
 * 규칙 평가는 앱 로드 시 한 번만 돈다 — 여섯 화면이 같은 평가 결과를 읽는다.
 * 스타일은 ui-kit 토큰·프리미티브만 쓴다(자체 팔레트 금지). 색은 상태 신호 전용이고,
 * 출처·목데이터 같은 메타는 무채색 chip 으로 낸다.
 */
import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { BadgeTone } from 'ui-kit';
import { evaluate } from '../../rules/evaluate';
import type { Facts, Incident, RunbookEntry, Severity, Violation } from '../../rules/types';
import factsJson from '../../rules/facts-snapshot.json';

/* 스냅샷에는 규칙이 읽지 않는 표시 전용 축도 들어 있다 */
interface FunnelStep {
  stage: string;
  value: number;
  unit: string;
  note?: string;
}
interface DeliveryFacts {
  coverage_0803: { published_without_new_delivery: number; new_delivery_now_nonpublished: number };
  integrity_0803: { delivery_rows: number };
}
export type ConsoleFacts = Facts & { news_funnel: FunnelStep[]; delivery: DeliveryFacts };

export const F = factsJson as unknown as ConsoleFacts;
export const EV = evaluate(F);
export const VIOLATIONS = EV.violations;
export const INCIDENTS = EV.incidents;
export const P0 = INCIDENTS.filter((i) => i.sev === 'P0');

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

export function runbookOf(v: Violation): RunbookEntry | undefined {
  return F.runbook[`${v.rule}.${v.target}`] ?? F.runbook[v.rule];
}

/** L2 툴팁 본문 — 근거·연쇄·조치. 네이티브 title 을 쓴다(GridPage 와 같은 관용구). */
export function violationTip(v: Violation, I?: Incident): string {
  const lines = [
    `${v.ruleName} (${v.rule}) · ${v.layer} 층 · ${v.sev}`,
    `대상: ${v.target}`,
    `왜: ${v.why}`,
  ];
  if (v.list) lines.push(`목록: ${v.list.join(' · ')}`);
  if (v.lastok) lines.push(`마지막 정상: ${kst(v.lastok)} · 귀결률 ${v.okrate}`);
  lines.push(`근거: ${v.evidence}`);
  if (I && I.members.length) {
    lines.push('', `이 사건에 묶인 위반 ${I.members.length}건 — 인과로 연결돼 카드 하나로 세웠다`);
    for (const m of I.members) lines.push(`  · ${m.v.rule} ${m.v.title} ← ${m.why}`);
  }
  const rb = runbookOf(v);
  lines.push('', `조치: ${rb ? rb.cmd + (rb.note ? ` — ${rb.note}` : '') : '런북 미등록'}`);
  if (v.mock) lines.push(`MOCK — 이 규칙은 목데이터에 의존한다. 실제 계측: ${v.dep ?? '—'}`);
  if (v.seed) lines.push('SEED — 로컬 시드 유래');
  return lines.join('\n');
}

/** 드릴다운 대상 — 규칙 위반의 drill 축을 실제 라우트로 옮긴다 */
export const DRILL_ROUTE: Record<string, string> = {
  run: '/ops/runs',
  chain: '/ops/chain',
  dataset: '/ops/datasets',
  trend: '/ops/trend',
  delivery: '/ops/delivery',
};
export const drillHref = (v: Violation): string =>
  `${DRILL_ROUTE[v.drill[0]] ?? '/'}?focus=${encodeURIComponent(v.drill[1])}`;

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

/** ⓘ — L2 정보 공개 손잡이 */
export function Info({ tip }: { tip: string }) {
  return (
    <span className="ops-i" title={tip} aria-label="설명">
      i
    </span>
  );
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
