/* 오늘 운영 요약 — 콘솔 홈 (ALPHA-738).
 *
 * 운영자가 묻는 순서대로 답한다:
 *   1. 지금 가장 중요한 운영 문제는?      → 최우선 운영 문제
 *   2. P0·P1·P2 는 각각 몇 건인가?        → 심각도 요약
 *   3. 각 심각도에 어떤 사건이 있는가?     → 심각도별 사건 목록
 *   4. 장중·배치·전달은 지금 어떤가?       → 운영 상태(레인을 섞지 않는다)
 *   5. 무슨 규칙·근거로 판정했는가?        → 탐지 상태(하단)
 *
 * 탐지 시스템의 내부 처리(규칙 수·위반 수·인과 병합 순서)는 지우지 않고 하단 탐지 상태로
 * 내렸다 — 운영 상황보다 먼저 읽힐 정보가 아니다.
 *
 * 카드는 여전히 사람이 고르지 않는다. 최우선 문제도 새 순위를 만들지 않고 기존 정렬
 * (심각도 → 연쇄 크기 → 수치)의 첫 사건이다.
 */
import { Link, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { StatusBadge } from 'ui-kit';
import { RULES } from '../../rules/rules';
import type { Incident, Severity } from '../../rules/types';
import {
  EV,
  F,
  INCIDENTS,
  Info,
  SEV_TONE,
  VIOLATIONS,
  drillHref,
  fmt,
  runbookOf,
  violationTip,
} from './shared';
import '../../styles/ops.css';

const SEVERITIES: Severity[] = ['P0', 'P1', 'P2'];
const SEV_MEANING: Record<Severity, string> = {
  P0: '지금 개입이 필요하다',
  P1: '오늘 안에 확인한다',
  P2: '기록해 두고 본다',
};

const bySeverity = (sev: Severity) => INCIDENTS.filter((i) => i.sev === sev);

/* ══ 1. 최우선 운영 문제 ══ */
function TopIssue({ incident: I }: { incident: Incident }) {
  const navigate = useNavigate();
  const v = I.root;
  const rb = runbookOf(v);
  return (
    <div className="card ops-top">
      <div className="card-head">
        <span className="t-label">최우선 운영 문제</span>
        <StatusBadge tone={SEV_TONE[I.sev]}>{I.sev}</StatusBadge>
        <span className="chip">{v.kls}</span>
        <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
          {v.rule}
        </span>
        {v.mock && <span className="chip">MOCK</span>}
        {v.seed && <span className="chip">SEED</span>}
        <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
          심각도 → 연쇄 크기 → 수치 순으로 가장 위인 사건입니다
          <Info tip={violationTip(v, I)} label={`${v.ruleName} 판정 근거`} />
        </span>
      </div>
      <div className="card-pad">
        <p className="t-h1 m-0">{v.title}</p>
        <p className="t-sm m-0" style={{ marginTop: 6 }}>
          <b className="ops-top-metric">{fmt(v.metric)}</b> <span style={{ color: 'var(--fg-3)' }}>{v.unit}</span>
          {' · '}
          {v.why}
        </p>

        {/* 영향 = 이 사건이 끌고 간 위반들. 새로 지어낸 문구가 아니라 인과 간선이 이미 이은 것이다 */}
        {I.members.length > 0 && (
          <div className="ops-top-impact">
            <span className="t-label">영향</span>
            <ul className="t-sm">
              {I.members.map((m) => (
                <li key={m.v.vid}>
                  {m.v.title} <span style={{ color: 'var(--fg-3)' }}>— {m.why}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 10 }}>
          <b style={{ color: 'var(--fg-2)' }}>조치</b>{' '}
          {rb ? (
            <>
              <code>{rb.cmd}</code>
              {rb.note && ` — ${rb.note}`}
            </>
          ) : (
            '런북 미등록'
          )}
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          <b style={{ color: 'var(--fg-2)' }}>근거</b> {v.evidence}
        </p>

        <button type="button" className="btn btn-primary" style={{ marginTop: 12 }} onClick={() => navigate(drillHref(v))}>
          사건 상세 보기 →
        </button>
      </div>
    </div>
  );
}

/* ══ 2. 심각도 요약 ══ */
function SeveritySummary({ onJump }: { onJump: (sev: Severity) => void }) {
  return (
    <div className="ops-sev-row">
      {SEVERITIES.map((sev) => {
        const n = bySeverity(sev).length;
        return (
          <button key={sev} type="button" className="kpi ops-sev" onClick={() => onJump(sev)} disabled={n === 0}>
            <span className="kpi-label">
              <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
            </span>
            <span className="kpi-value">{n}건</span>
            <span className="kpi-sub">{SEV_MEANING[sev]}</span>
          </button>
        );
      })}
      <div className="kpi">
        <span className="kpi-label">전체 사건</span>
        <span className="kpi-value">{INCIDENTS.length}건</span>
        <span className="kpi-sub">인과로 묶인 조치 단위</span>
      </div>
    </div>
  );
}

/* ══ 3. 심각도별 사건 ══ */
function SeveritySection({ sev, open, onToggle }: { sev: Severity; open: boolean; onToggle: () => void }) {
  const navigate = useNavigate();
  const list = bySeverity(sev);
  if (list.length === 0) return null;
  return (
    <div className="card" id={`sev-${sev}`}>
      <button type="button" className="card-head ops-sev-head" aria-expanded={open} onClick={onToggle}>
        <span aria-hidden="true" style={{ color: 'var(--fg-3)' }}>{open ? '▾' : '▸'}</span>
        <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
        <span className="t-label">사건 {list.length}건</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>{SEV_MEANING[sev]}</span>
      </button>
      {open && (
        <table className="table">
          <thead>
            <tr>
              <th>사건</th>
              <th className="num">수치</th>
              <th>단위</th>
              <th>분류</th>
              <th>규칙</th>
              <th>연쇄</th>
              <th>상세</th>
            </tr>
          </thead>
          <tbody>
            {list.map((I) => (
              <tr key={I.root.vid}>
                <td>
                  {I.root.title}
                  {I.root.mock && <span className="chip" style={{ marginLeft: 6 }}>MOCK</span>}
                  {I.root.seed && <span className="chip" style={{ marginLeft: 6 }}>SEED</span>}
                </td>
                <td className="num">{fmt(I.root.metric)}</td>
                <td className="col-muted">{I.root.unit}</td>
                <td className="col-muted">{I.root.kls}</td>
                <td className="mono col-muted">
                  {I.root.rule}
                  <Info tip={violationTip(I.root, I)} label={`${I.root.ruleName} 판정 근거`} />
                </td>
                <td className="col-muted">
                  {I.members.length > 0 ? (
                    <span className="chip chip-warn">+{I.members.length}</span>
                  ) : (
                    <span style={{ color: 'var(--fg-4)' }}>—</span>
                  )}
                </td>
                <td>
                  {/* (i) 는 판정 근거, 이 버튼이 사건 상세로 가는 동작 — 둘을 갈라 둔다 */}
                  <button type="button" className="ops-run-btn" onClick={() => navigate(drillHref(I.root))}>
                    상세 →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ══ 4. 운영 상태 — 레인을 섞지 않는다 ══ */
const LANE_TIP = [
  '배치와 장중은 같은 체인의 두 입력이지만 오늘의 상태는 서로 다르다 — 한 줄에 섞으면 어느 쪽이 깨졌는지 안 보인다.',
  '',
  '소비자 수신은 0 이 아니라 관측 불가다. 온프렘이 무엇을 읽었는지 확인할 채널이 클라우드에 없다 —',
  '"아무도 안 읽었다"와 "볼 수 없다"는 다른 사실이라 숫자 자리에 두지 않는다.',
].join('\n');

function LaneRow({
  label,
  from,
  fromLabel,
  to,
  toLabel,
  note,
  href,
  blocked,
}: {
  label: string;
  from: number;
  fromLabel: string;
  to: number;
  toLabel: string;
  note: string;
  href: string;
  blocked: boolean;
}) {
  const lost = from - to;
  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{label}</td>
      <td className="num">
        {fmt(from)} <span className="col-muted t-xs">{fromLabel}</span>
      </td>
      <td style={{ color: 'var(--fg-4)' }} aria-hidden="true">→</td>
      <td className="num">
        <span style={blocked ? { color: 'var(--down)', fontWeight: 700 } : undefined}>{fmt(to)}</span>{' '}
        <span className="col-muted t-xs">{toLabel}</span>
      </td>
      <td>
        {lost > 0 ? (
          <StatusBadge tone={blocked ? 'blocked' : 'warn'}>{blocked ? '전량 정지' : `−${lost} 유실`}</StatusBadge>
        ) : (
          <StatusBadge tone="active">유실 없음</StatusBadge>
        )}
      </td>
      <td className="col-muted t-xs">{note}</td>
      <td>
        <Link to={href} className="t-xs">
          상세 →
        </Link>
      </td>
    </tr>
  );
}

function OperationState() {
  const S = F.chain.stages;
  const batchFeed = F.chain.feeds[0];
  const intradayFeed = F.chain.feeds[1];
  const pub = S.find((s) => s.id === 'c.pub');
  const dlv = S.find((s) => s.id === 'c.dlv');
  const firstStage = S.find((s) => !s.blind);
  const consumer = S.find((s) => s.blind);

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">운영 상태</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          배치 · 장중 · 전달을 각각 읽습니다
          <Info tip={LANE_TIP} label="레인 구분" />
        </span>
      </div>
      <table className="table">
        <tbody>
          <LaneRow
            label="배치"
            from={batchFeed?.v ?? 0}
            fromLabel="트리거"
            to={pub?.batch ?? 0}
            toLabel="게시"
            note="트리거된 ETF 중 설명이 게시된 수"
            href="/ops/chain"
            blocked={(pub?.batch ?? 0) === 0 && (batchFeed?.v ?? 0) > 0}
          />
          <LaneRow
            label="장중"
            from={intradayFeed?.v ?? 0}
            fromLabel="트리거"
            to={firstStage?.intraday ?? 0}
            toLabel="관측 진입"
            note="체인에 들어가지 못한 것이지 체인에서 실패한 것이 아닙니다"
            href="/ops/chain"
            blocked={(firstStage?.intraday ?? 0) === 0 && (intradayFeed?.v ?? 0) > 0}
          />
          <LaneRow
            label="전달"
            from={pub?.batch ?? 0}
            fromLabel="게시"
            to={dlv?.batch ?? 0}
            toLabel="발번"
            note="게시와 발번은 같은 트랜잭션이라 구조상 같아야 합니다"
            href="/ops/delivery"
            blocked={false}
          />
        </tbody>
      </table>
      <div className="card-pad" style={{ paddingTop: 0 }}>
        {/* 관측 불가를 숫자 자리(체인 끝값)에 두지 않는다 — 0 과 혼동된다 */}
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          <b style={{ color: 'var(--fg-2)' }}>{consumer?.label ?? '소비자 수신'}</b> —{' '}
          <StatusBadge tone="neutral">관측 불가</StatusBadge> 접근 채널이 없어 볼 수 없습니다. 0건이 아닙니다.
        </p>
      </div>
    </div>
  );
}

/* ══ 5. 탐지 상태 — 상단에서 내려온 규칙 엔진 내부 정보 ══ */
const GEN_TIP = [
  '이 화면이 만들어지는 순서',
  `1. 규칙 ${RULES.length}개를 오늘 사실 위에서 각각 평가 → 위반 목록`,
  '2. 인과 간선으로 위반을 사건으로 병합 (같은 런의 재시도 소진은 런 실패의 결과, 장중 체인 손실은 소비자 부재의 결과)',
  '3. 사건을 심각도 → 연쇄 크기 → 영향 수치순 정렬',
  '',
  '고정된 사건은 하나도 없다. 규칙이 안 걸리면 사건도 안 나온다.',
].join('\n');

const ABSENCE_TIP = [
  '부재 4구분',
  '0 — 실측 0',
  '— — 집계 없음',
  '관측 불가 — 접근 채널이 없다',
  '계측 없음 — 기록을 남기지 않는다',
  '',
  '출처: DB 원장 · S3 로그 · AWS 제어면 · CODE 설정 · SEED(로컬 시드) · MOCK(목데이터)',
  'MOCK 배지 개수가 곧 남은 계측 부채다.',
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
          목데이터에 의존하는 규칙 {mockRules}개 · 사실 축이 없어 평가하지 못한 규칙 {unevaluated}개 — 이 수가 곧 남은
          계측 부채입니다.
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
  const [open, setOpen] = useState<Record<string, boolean>>({ P0: true, P1: false, P2: false });
  const top = INCIDENTS[0];

  const jump = (sev: Severity) => {
    setOpen((s) => ({ ...s, [sev]: true }));
    requestAnimationFrame(() => {
      document.getElementById(`sev-${sev}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 기준 시각만 간결하게 — 규칙 엔진 설명은 하단 탐지 상태로 내렸다 */}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        거래일 {F.meta.today} · DB {hm(F.meta.db)} · AWS/S3 {hm(F.meta.aws)}
      </p>

      {top ? (
        <TopIssue incident={top} />
      ) : (
        <div className="card card-pad">
          <p className="t-h2 m-0">확인할 운영 문제가 없습니다</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            규칙 {RULES.length}개가 오늘 사실 위에서 돌았고 걸린 사건이 없습니다 — 아래 탐지 상태에서 각 규칙의 결과를
            확인할 수 있습니다.
          </p>
        </div>
      )}

      <SeveritySummary onJump={jump} />

      {SEVERITIES.map((sev) => (
        <SeveritySection
          key={sev}
          sev={sev}
          open={!!open[sev]}
          onToggle={() => setOpen((s) => ({ ...s, [sev]: !s[sev] }))}
        />
      ))}

      <OperationState />

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
