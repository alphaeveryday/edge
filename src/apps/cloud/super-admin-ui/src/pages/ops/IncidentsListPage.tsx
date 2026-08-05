/* 문제·사건 — 전체 사건 목록 (ALPHA-738).
 *
 * 오늘 페이지에서 떼어낸 화면이다. `/` 는 "지금 무슨 상황인가"의 요약이고, 여기는
 * "걸린 사건 전부를 훑는다" — 두 역할이 한 화면에 있으면 요약이 목록에 묻힌다.
 *
 * 심각도는 **필터**다. 세 목록을 동시에 펼치면 이 화면도 다시 긴 나열이 된다 —
 * 한 번에 하나만 보고, 무엇을 보고 있는지는 URL(?severity=)이 말한다.
 *
 * 판정·정렬은 여기서 아무것도 새로 만들지 않는다. evaluate() 가 낸 사건과 그 순서 그대로다.
 */
import { useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { Severity } from '../../rules/types';
import { INCIDENTS, Info, SEV_TONE, drillHref, fmt, runbookOf, violationTip } from './shared';
import '../../styles/ops.css';

const SEVERITIES: Severity[] = ['P0', 'P1', 'P2'];
const SEV_MEANING: Record<Severity, string> = {
  P0: '지금 개입이 필요하다',
  P1: '오늘 안에 확인한다',
  P2: '기록해 두고 본다',
};
const isSeverity = (v: string | null): v is Severity => v === 'P0' || v === 'P1' || v === 'P2';
const bySeverity = (sev: Severity) => INCIDENTS.filter((i) => i.sev === sev);

export function IncidentsListPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const requested = params.get('severity');
  /* URL 이 선택 상태의 정본이다 — 새로고침·링크 공유가 같은 심각도를 연다. 기본은 P0. */
  const selected: Severity = isSeverity(requested) ? requested : 'P0';
  const list = bySeverity(selected);

  /* 화면만 P0 로 떨어뜨리면 주소창이 거짓말을 한다(?severity=foo 인데 P0 를 보여줌).
   * 링크를 공유했을 때도 같은 상태가 열리도록 URL 을 정규화한다. */
  useEffect(() => {
    if (isSeverity(requested)) return;
    const next = new URLSearchParams(params);
    next.set('severity', 'P0');
    setParams(next, { replace: true });
  }, [requested, params, setParams]);

  const select = (sev: Severity) => {
    const next = new URLSearchParams(params);
    next.set('severity', sev);
    /* 필터 전환이지 페이지 이동이 아니라 히스토리를 쌓지 않는다 */
    setParams(next, { replace: true });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="card card-pad">
        <p className="t-sm m-0">심각도를 골라 그 사건만 봅니다.</p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          {/* 전체 개수는 보조 문구로만 — 단순 합계를 핵심 카드로 세우지 않는다 */}
          규칙이 오늘 잡은 사건은 모두 {INCIDENTS.length}건입니다. 정렬은 evaluate() 가 낸 순서(심각도 →
          연쇄 크기 → 대표 수치) 그대로이고, 단위가 다른 수치를 가로질러 비교하지 않으므로 같은 심각도
          안의 위아래를 조치 우선순위로 읽지 마세요.
          {' · '}
          <Link to="/">오늘 운영 요약으로</Link>
        </p>
      </div>

      {/* 선택 컨트롤 — 진짜 버튼이라 Tab·Enter·Space 가 동작하고, 색 외에 표식·aria-pressed 로도 선택을 말한다 */}
      <div className="ops-sev-row" role="group" aria-label="심각도 선택">
        {SEVERITIES.map((sev) => {
          const on = sev === selected;
          const n = bySeverity(sev).length;
          return (
            <button
              key={sev}
              type="button"
              className={'kpi ops-sev' + (on ? ' ops-sev-on' : '')}
              aria-pressed={on}
              onClick={() => select(sev)}
            >
              <span className="kpi-label">
                <span aria-hidden="true" className="ops-sev-mark">
                  {on ? '▶' : ' '}
                </span>
                <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
                {on && <span className="chip chip-accent">보는 중</span>}
              </span>
              <span className="kpi-value">{n}건</span>
              <span className="kpi-sub">{SEV_MEANING[sev]}</span>
            </button>
          );
        })}
      </div>

      <div className="card">
        <div className="card-head">
          <StatusBadge tone={SEV_TONE[selected]}>{selected}</StatusBadge>
          <span className="t-label">사건 {list.length}건</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {SEV_MEANING[selected]}
          </span>
        </div>
        {list.length === 0 ? (
          <div className="card-pad">
            <p className="t-sm m-0">{selected} 로 걸린 사건이 없습니다.</p>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
              규칙이 이 심각도로 판정한 것이 오늘 없다는 뜻입니다 — 다른 심각도를 골라 보세요.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>사건</th>
                  <th className="num">수치</th>
                  <th>단위</th>
                  <th>분류</th>
                  <th>규칙</th>
                  <th>연쇄</th>
                  <th>권장 조치</th>
                  <th>상세</th>
                </tr>
              </thead>
              <tbody>
                {list.map((I) => {
                  const rb = runbookOf(I.root);
                  return (
                    <tr key={I.root.vid}>
                      <td>
                        {I.root.title}
                        {I.root.mock && (
                          <span className="chip" style={{ marginLeft: 6 }}>
                            MOCK
                          </span>
                        )}
                        {I.root.seed && (
                          <span className="chip" style={{ marginLeft: 6 }}>
                            SEED
                          </span>
                        )}
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
                      <td className="col-muted t-xs">
                        {rb ? <code>{rb.cmd}</code> : '런북 미등록'}
                      </td>
                      <td>
                        {/* (i) 는 판정 근거, 이 버튼이 근거 화면으로 가는 동작 — 둘을 갈라 둔다 */}
                        <button
                          type="button"
                          className="ops-run-btn"
                          onClick={() => navigate(drillHref(I.root))}
                        >
                          상세 →
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
