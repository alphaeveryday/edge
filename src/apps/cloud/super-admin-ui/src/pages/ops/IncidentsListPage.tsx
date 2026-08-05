/* 문제·사건 — 전체 사건 목록 (ALPHA-738).
 *
 * 오늘 페이지에서 떼어낸 화면이다. `/` 는 "지금 무슨 상황인가"의 요약이고, 여기는
 * "걸린 사건 전부를 훑는다" — 두 역할이 한 화면에 있으면 요약이 목록에 묻힌다.
 *
 * 판정·정렬은 여기서 아무것도 새로 만들지 않는다. evaluate() 가 낸 사건과 그 순서를
 * 심각도로 묶어 보여줄 뿐이다.
 */
import { useEffect, useState } from 'react';
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
const isSeverity = (v: string | null): v is Severity =>
  v === 'P0' || v === 'P1' || v === 'P2';

const bySeverity = (sev: Severity) => INCIDENTS.filter((i) => i.sev === sev);

function SeveritySection({
  sev,
  open,
  onToggle,
}: {
  sev: Severity;
  open: boolean;
  onToggle: () => void;
}) {
  const navigate = useNavigate();
  const list = bySeverity(sev);
  return (
    <div className="card" id={`sev-${sev}`}>
      <button type="button" className="card-head ops-sev-head" aria-expanded={open} onClick={onToggle}>
        <span aria-hidden="true" style={{ color: 'var(--fg-3)' }}>
          {open ? '▾' : '▸'}
        </span>
        <StatusBadge tone={SEV_TONE[sev]}>{sev}</StatusBadge>
        <span className="t-label">사건 {list.length}건</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {SEV_MEANING[sev]}
        </span>
      </button>
      {open &&
        (list.length === 0 ? (
          <div className="card-pad">
            <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
              이 심각도로 걸린 사건이 없습니다.
            </p>
          </div>
        ) : (
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
        ))}
    </div>
  );
}

export function IncidentsListPage() {
  const [params] = useSearchParams();
  const requested = params.get('severity');
  const focus = isSeverity(requested) ? requested : null;

  /* 지목된 심각도만 열고 나머지는 접는다. 지목이 없으면 P0 만 열어 둔다 —
   * 전부 펼치면 이 화면도 다시 "긴 나열"이 된다. */
  const [open, setOpen] = useState<Record<string, boolean>>(() => ({
    P0: focus === null || focus === 'P0',
    P1: focus === 'P1',
    P2: focus === 'P2',
  }));

  /* 심각도 요약에서 넘어온 주소로 들어오면 그 그룹이 바로 보여야 한다 */
  useEffect(() => {
    if (!focus) return;
    setOpen((s) => ({ ...s, [focus]: true }));
    requestAnimationFrame(() => {
      document.getElementById(`sev-${focus}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, [focus]);

  return (
    <div className="flex flex-col gap-4">
      <div className="card card-pad">
        <p className="t-sm m-0">
          규칙이 오늘 사실 위에서 잡은 사건 <b>{INCIDENTS.length}건</b> 전부입니다 — 심각도로 묶었습니다.
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          정렬은 evaluate() 가 낸 순서(심각도 → 연쇄 크기 → 대표 수치) 그대로입니다. 단위가 다른 수치를
          가로질러 비교하지 않으므로 같은 심각도 안의 위아래를 조치 우선순위로 읽지 마세요.
          {' · '}
          <Link to="/">오늘 운영 요약으로</Link>
        </p>
      </div>

      {SEVERITIES.map((sev) => (
        <SeveritySection
          key={sev}
          sev={sev}
          open={!!open[sev]}
          onToggle={() => setOpen((s) => ({ ...s, [sev]: !s[sev] }))}
        />
      ))}
    </div>
  );
}
