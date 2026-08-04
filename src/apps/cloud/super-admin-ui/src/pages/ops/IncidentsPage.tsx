/* 오늘 사건 — 콘솔 홈 (ALPHA-738).
 *
 * 카드는 사람이 고르지 않는다: 규칙 위반 → 인과 병합 → 심각도·연쇄 크기·수치순 정렬의 렌더링이다.
 * 규칙이 안 걸리면 카드도 없다. 상단 판정 문장도 evaluate() 출력에서 만든다(하드코딩 0).
 *
 * 정보 공개는 3단이다 — L1 숫자만, L2 ⓘ·숫자 호버 툴팁, L3 클릭 시 축 화면의 해당 행으로.
 */
import { Link, useNavigate } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { RULES } from '../../rules/rules';
import type { Incident } from '../../rules/types';
import { ChainStrip } from './ChainStrip';
import {
  EV,
  F,
  INCIDENTS,
  Info,
  P0,
  SEV_TONE,
  VIOLATIONS,
  drillHref,
  fmt,
  kst,
  violationTip,
} from './shared';
import '../../styles/ops.css';

const GEN_TIP = [
  '이 화면이 만들어지는 순서',
  `1. 규칙 ${RULES.length}개를 오늘 사실 위에서 각각 평가 → 위반 목록`,
  '2. 인과 간선으로 위반을 사건으로 병합 (같은 런의 재시도 소진은 런 실패의 결과, 장중 체인 손실은 소비자 부재의 결과)',
  '3. 사건을 심각도 → 연쇄 크기 → 영향 수치순 정렬',
  '4. 상위를 카드로, 나머지를 접힌 목록으로',
  '',
  '고정된 카드는 하나도 없다. 규칙이 안 걸리면 카드도 안 나온다.',
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

function IncidentCard({ I }: { I: Incident }) {
  const navigate = useNavigate();
  const v = I.root;
  return (
    <button type="button" className="card ops-card" onClick={() => navigate(drillHref(v))}>
      {/* ⓘ 를 규칙 id 바로 옆에 둔다 — 오른쪽 끝에 밀어두면 칩이 늘어난 카드에서 줄이 바뀐다 */}
      <div className="ops-card-head">
        <StatusBadge tone={SEV_TONE[I.sev]}>{I.sev}</StatusBadge>
        <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
          {v.rule}
        </span>
        <Info tip={violationTip(v, I)} label={`${v.ruleName} 사건`} />
        <span className="chip">{v.kls}</span>
        {I.members.length > 0 && (
          <span className="chip chip-warn" title="인과로 이어진 위반이 이 카드 하나로 접혔다 — 조치는 하나다">
            연쇄 +{I.members.length}
          </span>
        )}
        {v.mock && <span className="chip">MOCK</span>}
        {v.seed && <span className="chip">SEED</span>}
      </div>
      <div className="ops-card-body">
        <div className="t-sm" style={{ fontWeight: 600 }}>
          {v.title}
        </div>
        <div className={'ops-metric' + (typeof v.metric === 'number' ? '' : ' ops-metric-text')}>
          {fmt(v.metric)}
        </div>
        <div className="ops-metric-unit">{v.unit}</div>
        <span className="ops-card-go">상세 →</span>
      </div>
    </button>
  );
}

export function IncidentsPage() {
  const navigate = useNavigate();
  const top = Math.max(P0.length, 3);
  const rest = INCIDENTS.slice(top);
  const pub = F.chain.stages.find((s) => s.id === 'c.pub');
  const firstStage = F.chain.stages.find((s) => !s.blind);
  const intradayFeed = F.chain.feeds[1];
  const intradayStalled = intradayFeed && intradayFeed.v > 0 && firstStage?.intraday === 0;
  const mockRules = EV.rules.filter((r) => r.depends_on_mock).length;

  return (
    <div className="flex flex-col gap-4">
      {/* ── 판정 문장 — 규칙 결과에서 생성된다 ── */}
      <div className="card card-pad">
        <p className="t-h2 m-0">
          규칙 {RULES.length}개가 위반 <b style={{ color: 'var(--down)' }}>{VIOLATIONS.length}건</b>을 잡았고, 인과로
          묶어 사건 <b style={{ color: 'var(--down)' }}>{INCIDENTS.length}건</b>입니다 — 그중 P0{' '}
          <b style={{ color: 'var(--down)' }}>{P0.length}건</b>.
          {pub?.batch != null && ` 오늘 게시 ${fmt(pub.batch)}종`}
          {intradayStalled && `, 장중 트리거 ${fmt(intradayFeed.v)}건은 체인 진입 전 정지`}.
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
          카드는 손으로 고른 게 아니라 규칙 위반 → 인과 병합 → 심각도·연쇄 크기순으로 생성됩니다. 내일 뉴스가 아니라
          공시가 깨지면 카드도 그쪽으로 바뀝니다.
          <Info tip={GEN_TIP} label="화면 생성 순서" />
          {'  '}기준 DB {kst(F.meta.db)} · AWS/S3 {kst(F.meta.aws)} · 거래일 {F.meta.today}
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          <span className="chip">MOCK</span> 계측이 없어 목데이터로 돌고 있는 규칙 {mockRules}개 — 이 수가 곧 남은 계측
          부채입니다.
          <Info tip={ABSENCE_TIP} label="부재 4구분·출처" />
        </p>
      </div>

      {/* ── 사건 카드 ── */}
      <div className="ops-cards">
        {INCIDENTS.slice(0, top).map((I) => (
          <IncidentCard key={I.root.vid} I={I} />
        ))}
      </div>

      {rest.length > 0 && (
        <details className="card">
          <summary
            className="t-sm"
            style={{ cursor: 'pointer', padding: 'var(--sp-4) var(--sp-6)', color: 'var(--fg-2)' }}
          >
            나머지 사건 {rest.length}건 — P1 {rest.filter((x) => x.sev === 'P1').length} · P2{' '}
            {rest.filter((x) => x.sev === 'P2').length}
          </summary>
          <table className="table">
            <thead>
              <tr>
                <th>사건</th>
                <th>심각도</th>
                <th>규칙</th>
                <th>분류</th>
                <th className="num">수치</th>
                <th>단위</th>
              </tr>
            </thead>
            <tbody>
              {rest.map((I) => (
                <tr
                  key={I.root.vid}
                  onClick={() => navigate(drillHref(I.root))}
                  style={{ cursor: 'pointer' }}
                  title={violationTip(I.root, I)}
                >
                  <td>
                    {I.root.title}
                    {I.members.length > 0 && (
                      <span className="chip chip-warn" style={{ marginLeft: 6 }}>
                        연쇄 +{I.members.length}
                      </span>
                    )}
                  </td>
                  <td>
                    <StatusBadge tone={SEV_TONE[I.sev]}>{I.sev}</StatusBadge>
                  </td>
                  <td className="mono col-muted">{I.root.rule}</td>
                  <td className="col-muted">{I.root.kls}</td>
                  <td className="num">{fmt(I.root.metric)}</td>
                  <td className="col-muted">{I.root.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {/* ── 설명 생산 체인 (요약) ── */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">설명 생산 체인</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            각 칸은 배치 / 장중 · <Link to="/ops/chain">단계별 상세</Link>
          </span>
        </div>
        <ChainStrip />
      </div>

      {/* ── 규칙 실행 결과 — 오늘 조용한 규칙도 남는다 ── */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">규칙 실행 결과</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            규칙은 코드에, 사실은 데이터에 — 위 카드는 이 결과의 렌더링일 뿐. "DLQ를 안 본 것"과 "봤는데 0인 것"은
            다르므로 위반 0인 규칙도 남습니다.
          </span>
        </div>
        <div className="card-pad">
          <div className="ops-rulebar">
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
                  className={
                    'chip' + (!R.evaluated ? '' : R.violations ? ' chip-down' : '')
                  }
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
        </div>
      </div>
    </div>
  );
}
