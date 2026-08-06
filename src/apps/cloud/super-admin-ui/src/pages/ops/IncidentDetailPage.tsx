/* 사건 상세 — 조사의 출발점 (ALPHA-738).
 *
 * 문제 카드의 "상세"가 같은 자리에서 긴 글을 펼치고 끝나면 조사가 시작되지 않는다. 이 화면은
 * **다음에 무엇을 열어야 하는가**까지 답하고, 그 다음 화면들은 기존 것을 그대로 쓴다
 * (실행 → /ops/runs · 세션 → /minute · 데이터셋 → /ops/datasets · 원장 근거 → /sources).
 *
 * 라우트라서 뒤로 가기·딥링크·새로고침이 그대로 동작한다.
 *
 * 지키는 선:
 *   · 판정을 새로 만들지 않는다 — 심각도·연쇄·근거는 evaluate() 결과 그대로다.
 *   · 없는 축을 지어내지 않는다. 최초 탐지 시각은 위반 단위 계측이 없어 "계측 없음"이다
 *     (스냅샷은 평가 시점만 안다).
 *   · 실행이 없는 사건은 실행 화면으로 보내지 않는다 — investigate() 가 그렇게 판정한다.
 */
import { Link, useParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { RULES } from '../../rules/rules';
import {
  Absent,
  F,
  INCIDENTS,
  Info,
  SEV_TONE,
  SourceChip,
  domainOf,
  fmt,
  kst,
  runbookOf,
} from './shared';
import { investigate, ledgerHref } from './investigation';
import '../../styles/ops.css';

const TARGET_LABEL: Record<string, string> = {
  run: '실행',
  slot: '예정 슬롯',
  session: '실시간 세션',
  dataset: '데이터셋',
  queue: '큐 · 배포',
  output: '산출 · 흐름',
};

function Fact({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="t-xs ops-fact">
      <span style={{ color: 'var(--fg-3)' }}>{k}</span>
      <span style={{ color: 'var(--fg-1)' }}>{children}</span>
    </div>
  );
}

export function IncidentDetailPage() {
  const { vid = '' } = useParams();
  const incident = INCIDENTS.find((i) => i.root.vid === vid);

  if (!incident) {
    return (
      <div className="card card-pad">
        <p className="t-sm m-0">이 사건을 찾을 수 없습니다.</p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          사건 식별자 <code>{vid || '(없음)'}</code> 가 지금 평가 결과에 없습니다 — 규칙이 더는 걸리지
          않거나(해소) 링크가 낡았습니다. 다른 사건으로 대체해 보여주지 않습니다.{' '}
          <Link to="/ops/incidents">문제·사건으로</Link>
        </p>
      </div>
    );
  }

  const v = incident.root;
  const rule = RULES.find((r) => r.id === v.rule);
  const { targets, ledger, ledgerNote } = investigate(incident, F);
  const ledgerTo = ledgerHref(ledger);
  const rb = runbookOf(v);

  return (
    <div className="flex flex-col gap-4">
      {/* ── breadcrumb — 실제 식별자로만 만든다 ── */}
      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        <Link to="/ops/incidents">문제·사건</Link>
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>{v.title}</span>
      </nav>

      {/* ── 1. 사건 ── */}
      <div className="card">
        <div className="card-head">
          <StatusBadge tone={SEV_TONE[incident.sev]}>{incident.sev}</StatusBadge>
          <span className="t-h3">{v.title}</span>
          <span className="chip chip-accent">{domainOf(v)}</span>
          <span className="chip">{v.kls}</span>
          {v.mock && <span className="chip">MOCK</span>}
          {v.seed && <span className="chip">SEED</span>}
          {rule && <SourceChip source={rule.source} />}
        </div>
        <div className="card-pad">
          <p className="t-sm m-0">
            <b>{fmt(v.metric)}</b> <span style={{ color: 'var(--fg-3)' }}>{v.unit}</span> ·{' '}
            <span style={{ color: 'var(--fg-2)' }}>{v.why}</span>
          </p>

          <div className="ops-facts" style={{ marginTop: 10 }}>
            <Fact k="대상">
              <span className="mono">{v.target}</span>
            </Fact>
            <Fact k="탐지 규칙">
              <span className="mono">{v.rule}</span> {v.ruleName} · {v.layer} 층
              {rule && <Info tip={`조건: ${rule.desc}\n기본 심각도: ${rule.base}`} label="규칙 조건" />}
            </Fact>
            <Fact k="현재 상태">평가 시점에 성립 — 이 위반은 지금 규칙에 걸려 있다</Fact>
            <Fact k="최근 관측">
              DB {kst(F.meta.db)} · AWS/S3 {kst(F.meta.aws)}
            </Fact>
            {/* 위반 단위 first_seen 계측이 없다 — 스냅샷은 평가 시점만 안다 */}
            <Fact k="최초 탐지">
              <Absent kind="uninstrumented" />
              <Info
                tip={'위반 단위의 최초 탐지 시각을 남기는 계측이 없다. 이 화면이 아는 것은 평가에 쓴 사실의 기준 시각뿐이라, "언제부터"를 만들어내지 않는다.'}
                label="최초 탐지"
              />
            </Fact>
            <Fact k="영향 범위">
              {v.list ? `${v.list.length}건 — ${v.list.join(' · ')}` : `${fmt(v.metric)} ${v.unit}`}
            </Fact>
            {v.lastok && (
              <Fact k="마지막 정상">
                {kst(v.lastok)} · 귀결률 {v.okrate ?? '—'}
              </Fact>
            )}
          </div>

          <p className="t-xs m-0" style={{ marginTop: 10, color: 'var(--fg-2)' }}>
            <b>판정 근거</b> — {v.evidence}
          </p>
          <p className="t-xs m-0" style={{ marginTop: 6 }}>
            <span className="t-label">조치</span>{' '}
            {rb ? (
              <code className="ops-p0-cmd">{rb.cmd}</code>
            ) : (
              <span style={{ color: 'var(--fg-3)' }}>런북 미등록</span>
            )}
            {rb?.note && <span style={{ color: 'var(--fg-3)' }}> — {rb.note}</span>}
          </p>
        </div>
      </div>

      {/* ── 2. 연쇄 — 흡수된 위반 ── */}
      {incident.members.length > 0 && (
        <div className="card">
          <div className="card-head">
            <span className="t-label">이 사건에 묶인 위반 {incident.members.length}건</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              인과 간선으로 접혔습니다 — 조치는 위 하나입니다
            </span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>규칙</th>
                <th>위반</th>
                <th>대상</th>
                <th>왜 이 사건의 결과인가</th>
              </tr>
            </thead>
            <tbody>
              {incident.members.map((m) => (
                <tr key={m.v.vid}>
                  <td className="mono">{m.v.rule}</td>
                  <td>{m.v.title}</td>
                  <td className="mono col-muted">{m.v.target}</td>
                  <td className="col-muted t-xs">{m.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── 3. 조사 다음 단계 ── */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">조사 다음 단계</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            이 사건이 실제로 들고 있는 식별자로만 엽니다 — 무관한 최근 실행을 붙이지 않습니다
          </span>
        </div>
        <div className="card-pad">
          {targets.length === 0 ? (
            <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
              이 사건에는 연결된 조사 대상이 없습니다 — 실행을 추측해 연결하지 않습니다.
            </p>
          ) : (
            <ul className="ops-target-list">
              {targets.map((t) => (
                <li key={`${t.kind}|${t.id}`}>
                  <Link to={t.href} className="ops-target">
                    <span className="chip chip-accent">{TARGET_LABEL[t.kind] ?? t.kind}</span>
                    <span className="t-sm" style={{ fontWeight: 600 }}>
                      {t.label}
                    </span>
                    <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                      {t.why}
                    </span>
                    <span className="t-xs ops-lane-link">열기 →</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {/* 원장 근거 — 조사의 마지막 단계다. 문맥이 없으면 링크를 만들지 않는다 */}
          <div className="ops-ledger-cta">
            {ledgerTo ? (
              <>
                <Link to={ledgerTo} className="btn btn-sm">
                  원장 근거 보기 →
                </Link>
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  이 사건의 문맥({[ledger?.runKey, ledger?.task, ledger?.dataset, ledger?.date]
                    .filter(Boolean)
                    .join(' · ')})으로 범위를 좁혀 원시 사실만 봅니다
                </span>
              </>
            ) : (
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                <b>원장 근거 없음</b> — {ledgerNote}
              </span>
            )}
          </div>
          {ledgerTo && ledgerNote && (
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
              {ledgerNote}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
