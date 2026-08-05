/* 오늘 운영 요약 — 콘솔 홈 (ALPHA-738).
 *
 * 이 화면은 "지금 무슨 상황이고 당장 뭘 봐야 하나"까지만 답한다. 걸린 사건 전부를 훑는 일은
 * 문제·사건(/ops/incidents) 소관이다 — 두 역할이 한 화면에 있으면 요약이 목록에 묻힌다.
 *
 * 순서: 기준 시각 → 운영 상태 → 즉시 조치 필요(P0) → 심각도 요약 → 탐지 상태(접힘).
 *
 * 판정은 아무것도 새로 만들지 않는다. 운영 상태의 정상/저하/차단은 응답이 준 두 수치의
 * 직접 비교이고, 사건은 evaluate() 결과 그대로다. 종합 점수나 최우선 순위를 세우지 않는다.
 */
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { RULES } from '../../rules/rules';
import type { Incident, Severity } from '../../rules/types';
import { EV, F, INCIDENTS, Info, SEV_TONE, VIOLATIONS, drillHref, fmt, runbookOf, violationTip } from './shared';
import '../../styles/ops.css';

const SEVERITIES: Severity[] = ['P0', 'P1', 'P2'];
const SEV_MEANING: Record<Severity, string> = {
  P0: '지금 개입이 필요하다',
  P1: '오늘 안에 확인한다',
  P2: '기록해 두고 본다',
};

/* ══ 1. 운영 상태 — compact strip ══
 *
 * 판정은 두 수치의 직접 비교다: 뒤 값이 0이고 앞이 있으면 차단, 줄었으면 저하, 같으면 정상.
 * 관측 채널이 없는 단계는 숫자 자리에 두지 않고 "관측 불가"로 낸다 — 0 과 다른 사실이다. */
type LaneVerdict = '정상' | '저하' | '차단' | '관측 불가';
const VERDICT_TONE: Record<LaneVerdict, BadgeTone> = {
  정상: 'active',
  저하: 'warn',
  차단: 'blocked',
  '관측 불가': 'neutral',
};

function verdictOf(from: number, to: number): LaneVerdict {
  if (from > 0 && to === 0) return '차단';
  if (to < from) return '저하';
  return '정상';
}

interface LaneCard {
  label: string;
  verdict: LaneVerdict;
  figure: string;
  note: string;
  href: string;
}

function laneCards(): LaneCard[] {
  const S = F.chain.stages;
  const batchFeed = F.chain.feeds[0];
  const intradayFeed = F.chain.feeds[1];
  const firstStage = S.find((s) => !s.blind);
  const pub = S.find((s) => s.id === 'c.pub');
  const dlv = S.find((s) => s.id === 'c.dlv');
  const consumer = S.find((s) => s.blind);

  const batchFrom = batchFeed?.v ?? 0;
  const batchTo = pub?.batch ?? 0;
  const intraFrom = intradayFeed?.v ?? 0;
  const intraTo = firstStage?.intraday ?? 0;
  const dlvFrom = pub?.batch ?? 0;
  const dlvTo = dlv?.batch ?? 0;

  return [
    {
      label: '배치',
      verdict: verdictOf(batchFrom, batchTo),
      figure: `트리거 ${fmt(batchFrom)} → 게시 ${fmt(batchTo)}`,
      note: '트리거된 ETF 중 설명이 게시된 수',
      href: '/ops/chain',
    },
    {
      label: '장중',
      verdict: verdictOf(intraFrom, intraTo),
      figure: `트리거 ${fmt(intraFrom)} → 관측 진입 ${fmt(intraTo)}`,
      note: '체인에 들어가지 못한 것이지 체인에서 실패한 것이 아니다',
      href: '/ops/chain',
    },
    {
      label: '전달',
      verdict: verdictOf(dlvFrom, dlvTo),
      figure: `게시 ${fmt(dlvFrom)} → 발번 ${fmt(dlvTo)}`,
      note: '같은 트랜잭션이라 구조상 같아야 한다',
      href: '/ops/delivery',
    },
    {
      label: consumer?.label ?? '소비자 수신',
      verdict: '관측 불가',
      figure: '접근 채널 없음',
      note: '온프렘이 무엇을 읽었는지 확인할 채널이 없다 — 0건이 아니다',
      href: '/ops/delivery',
    },
  ];
}

const LANE_TIP = [
  '판정은 두 수치의 직접 비교다 — 뒤 값이 0이고 앞이 있으면 차단, 줄었으면 저하, 같으면 정상.',
  '새 점수나 종합 등급을 만들지 않는다.',
  '',
  '배치와 장중은 같은 체인의 두 입력이지만 오늘의 상태는 서로 다르다 — 한 줄에 섞으면',
  '어느 쪽이 깨졌는지 안 보인다.',
  '',
  '소비자 수신은 0 이 아니라 관측 불가다. "아무도 안 읽었다"와 "볼 수 없다"는 다른 사실이라',
  '숫자 자리에 두지 않는다.',
].join('\n');

function OperationStrip() {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">운영 상태</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          배치 · 장중 · 전달을 각각 읽습니다
          <Info tip={LANE_TIP} label="레인 판정 기준" />
        </span>
      </div>
      <div className="card-pad">
        <div className="ops-lane-strip">
          {laneCards().map((l) => (
            <div key={l.label} className="ops-lane">
              <div className="ops-lane-head">
                <span className="t-label">{l.label}</span>
                {/* 배지에 점+글자가 함께 있어 색만으로 상태를 가르지 않는다 */}
                <StatusBadge tone={VERDICT_TONE[l.verdict]}>{l.verdict}</StatusBadge>
              </div>
              <div className="ops-lane-figure">{l.figure}</div>
              <div className="t-xs" style={{ color: 'var(--fg-3)' }}>
                {l.note}
              </div>
              <Link to={l.href} className="t-xs ops-lane-link">
                상세 →
              </Link>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ══ 2. 즉시 조치 필요 — P0 ══
 * 하나를 골라 크게 세우지 않는다. 정렬은 단위가 다른 수치를 가로질러 비교하므로
 * "가장 먼저 조치할 것"을 단정할 근거가 되지 못한다. */
function P0Item({ incident: I }: { incident: Incident }) {
  const navigate = useNavigate();
  const v = I.root;
  const rb = runbookOf(v);
  return (
    <li className="ops-p0">
      <div className="ops-p0-head">
        <span className="t-h3">{v.title}</span>
        <span className="chip">{v.kls}</span>
        <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
          {v.rule}
        </span>
        <Info tip={violationTip(v, I)} label={`${v.ruleName} 판정 근거`} />
        {I.members.length > 0 && (
          <span className="chip chip-warn" title="인과로 이어진 위반이 이 사건 하나로 접혔다 — 조치는 하나다">
            연쇄 +{I.members.length}
          </span>
        )}
        {v.mock && <span className="chip">MOCK</span>}
        {v.seed && <span className="chip">SEED</span>}
        <button
          type="button"
          className="btn btn-sm ops-p0-go"
          onClick={() => navigate(drillHref(v))}
        >
          상세 →
        </button>
      </div>
      <div className="t-sm ops-p0-metric">
        <b>{fmt(v.metric)}</b> <span style={{ color: 'var(--fg-3)' }}>{v.unit}</span>
        {' · '}
        <span style={{ color: 'var(--fg-2)' }}>{v.why}</span>
      </div>
      <div className="t-xs" style={{ color: 'var(--fg-3)' }}>
        <b style={{ color: 'var(--fg-2)' }}>조치</b>{' '}
        {rb ? (
          <>
            <code>{rb.cmd}</code>
            {rb.note && ` — ${rb.note}`}
          </>
        ) : (
          '런북 미등록'
        )}
        {' · '}
        <b style={{ color: 'var(--fg-2)' }}>근거</b> {v.evidence}
      </div>
    </li>
  );
}

function ImmediateAction({ list }: { list: Incident[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">즉시 조치 필요</span>
        <StatusBadge tone="blocked">P0 {list.length}건</StatusBadge>
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          <Link to="/ops/incidents">모든 문제 보기 →</Link>
        </span>
      </div>
      {list.length === 0 ? (
        <div className="card-pad">
          <p className="t-sm m-0">즉시 조치가 필요한 문제가 없습니다.</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            P0 사건이 0건입니다 — P1·P2 는 <Link to="/ops/incidents">문제·사건</Link>에서 확인합니다.
          </p>
        </div>
      ) : (
        <ul className="ops-p0-list">
          {list.map((I) => (
            <P0Item key={I.root.vid} incident={I} />
          ))}
        </ul>
      )}
    </div>
  );
}

/* ══ 3. 심각도 요약 — 클릭하면 문제·사건 화면으로 ══ */
function SeveritySummary() {
  return (
    <div className="ops-sev-row">
      {SEVERITIES.map((sev) => {
        const n = INCIDENTS.filter((i) => i.sev === sev).length;
        return (
          <Link key={sev} to={`/ops/incidents?severity=${sev}`} className="kpi ops-sev">
            <span className="kpi-label">
              <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
            </span>
            <span className="kpi-value">{n}건</span>
            <span className="kpi-sub">{SEV_MEANING[sev]}</span>
          </Link>
        );
      })}
      <Link to="/ops/incidents" className="kpi ops-sev">
        <span className="kpi-label">전체 사건</span>
        <span className="kpi-value">{INCIDENTS.length}건</span>
        <span className="kpi-sub">인과로 묶인 조치 단위</span>
      </Link>
    </div>
  );
}

/* ══ 4. 탐지 상태 — 최하단, 접힘 ══ */
const GEN_TIP = [
  '이 화면이 만들어지는 순서',
  `1. 규칙 ${RULES.length}개를 오늘 사실 위에서 각각 평가 → 위반 목록`,
  '2. 인과 간선으로 위반을 사건으로 병합 (같은 런의 재시도 소진은 런 실패의 결과, 장중 체인 손실은 소비자 부재의 결과)',
  '3. 사건을 심각도 → 연쇄 크기 → 대표 수치순 정렬',
  '',
  '고정된 사건은 하나도 없다. 규칙이 안 걸리면 사건도 안 나온다.',
  '이 정렬은 목록을 안정시키기 위한 것이지 조치 우선순위가 아니다 — 단위가 다른 수치를 가로질러 비교하지 않는다.',
].join('\n');

const ABSENCE_TIP = [
  '부재 4구분',
  '0 — 실측 0',
  '— — 집계 없음',
  '관측 불가 — 접근 채널이 없다',
  '계측 없음 — 기록을 남기지 않는다',
  '',
  '출처: DB 원장 · S3 로그 · AWS 제어면 · CODE 설정 · SEED(로컬 시드) · MOCK(목데이터)',
].join('\n');

function DetectionState() {
  const mockRules = EV.rules.filter((r) => r.depends_on_mock).length;
  const unevaluated = EV.rules.filter((r) => !r.evaluated).length;
  return (
    <details className="card">
      <summary className="t-sm ops-detect-summary">
        탐지 상태 — 규칙 {RULES.length}개 · 위반 {VIOLATIONS.length}건 · 인과 병합 후 사건{' '}
        {INCIDENTS.length}건
      </summary>
      <div className="card-pad" style={{ paddingTop: 0 }}>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          사건은 손으로 고른 것이 아니라 규칙 위반을 인과로 묶은 결과입니다.
          <Info tip={GEN_TIP} label="사건 생성 순서" />
          목데이터에 의존하는 규칙 {mockRules}개 · 사실 축이 없어 평가하지 못한 규칙 {unevaluated}개.
          <Info tip={ABSENCE_TIP} label="부재 4구분·출처" />
        </p>
        <div className="ops-rulebar" style={{ marginTop: 10 }}>
          {EV.rules.map((R) => {
            const rule = RULES.find((x) => x.id === R.id)!;
            const tip = [
              `${R.name} (${R.id}) · ${R.layer} 층 · 기본 ${rule.base}`,
              `조건: ${rule.desc}`,
              `분류: ${rule.kls}`,
              `결과: ${
                !R.evaluated
                  ? '평가 불가 — 필요한 사실 축이 없다(계측 없음은 위반 0이 아니다)'
                  : R.violations
                    ? `위반 ${R.violations}건`
                    : '위반 없음 — 이 규칙은 오늘 조용하다'
              }`,
              ...(rule.dep ? [`계측 의존: ${rule.dep} — 현재 목데이터로 대체`] : []),
              ...(R.note && R.note !== rule.dep ? [R.note] : []),
            ].join('\n');
            return (
              <span
                key={R.id}
                className={'chip' + (!R.evaluated ? '' : R.violations ? ' chip-down' : '')}
                title={tip}
              >
                <span className="mono">{R.id}</span> {R.name}
                {R.violations > 0 && ` · ${R.violations}`}
                {!R.evaluated && ' · 평가 불가'}
                {R.depends_on_mock && ' · MOCK'}
              </span>
            );
          })}
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
          위반 0인 규칙도 남깁니다 — "DLQ를 안 본 것"과 "봤는데 0인 것"은 다릅니다.
        </p>
      </div>
    </details>
  );
}

/* ══ 페이지 ══ */
export function IncidentsPage() {
  const [p0] = useState(() => INCIDENTS.filter((i) => i.sev === 'P0'));

  return (
    <div className="flex flex-col gap-4">
      {/* 기준 시각만 — 규칙 엔진 설명은 하단 탐지 상태에 있다 */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        거래일 {F.meta.today} · DB {hm(F.meta.db)} · AWS/S3 {hm(F.meta.aws)}
      </p>

      <OperationStrip />
      <ImmediateAction list={p0} />
      <SeveritySummary />
      <DetectionState />
    </div>
  );
}

/** 기준 시각은 시:분까지만 — 홈에서 초 단위는 읽히지 않는다 */
function hm(iso: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}
