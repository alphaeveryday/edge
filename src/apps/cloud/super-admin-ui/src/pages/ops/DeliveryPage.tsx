/* Cloud 게시·발번 경계 — Cloud 게시와 tenant_delivery 발번이 맞아떨어지는가 (ALPHA-738).
 *
 * 책임 경계(ADR-0026): Cloud 는 Event Store 적재·게시까지, 발번 이후는 온프렘 영역이다.
 * 소비자 수신은 측정된 지표가 아니라 **관측 범위 밖**이라 KPI 로 세우지 않고 안내로만 남긴다 —
 * 숫자 자리에 두면 0건·실패와 구분되지 않는다.
 */
import { Absent, AxisHeader, F, fmt, useFocusRow } from './shared';
import '../../styles/ops.css';

export function DeliveryPage() {
  useFocusRow();
  const cov = F.delivery.coverage_0803;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader
        question="Cloud 게시된 설명이 테넌트로 빠짐없이 발번됐는가?"
        note="Cloud 게시 → tenant_delivery 발번 구간만 답합니다"
      />

      <div className="ops-cards">
        <div className="kpi">
          <div className="kpi-label">전달 행</div>
          <div className="kpi-value">{fmt(F.delivery.integrity_0803.delivery_rows)}</div>
          <div className="kpi-sub">tenant_delivery 누적</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Cloud 게시·미발번</div>
          <div className="kpi-value">{fmt(cov.published_without_new_delivery)}</div>
          <div className="kpi-sub">구조상 0이어야 함</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">운영 테넌트</div>
          <div className="kpi-value">
            0 <span className="t-xs" style={{ fontWeight: 500, color: 'var(--fg-3)' }}>(+시드 1)</span>
          </div>
          <div className="kpi-sub">
            <span className="chip">SEED</span> 로컬 compose 시드 잔재
          </div>
        </div>
      </div>

      <div className="card" id="b-dlv">
        <div className="card-head">
          <span className="t-label">경계 정합 (R14)</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            게시와 발번은 같은 트랜잭션이라 어긋나면 구조 문제입니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>검사</th>
              <th className="num">건</th>
              <th>해석</th>
            </tr>
          </thead>
          <tbody>
            <tr id="b-pub">
              <td>Cloud 게시됐는데 미발번</td>
              <td className="num">{cov.published_without_new_delivery}</td>
              <td className="col-muted">같은 트랜잭션이라 구조상 0</td>
            </tr>
            <tr>
              <td>
                발번됐는데 현재 Cloud 비게시 <span className="chip">SEED</span>
              </td>
              <td className="num" style={{ color: 'var(--warn)' }}>
                {cov.new_delivery_now_nonpublished}
              </td>
              <td className="col-muted">{F.boundary.seed_note}</td>
            </tr>
            <tr>
              <td>소비 커서 행</td>
              <td className="num">
                {F.boundary.sync_cursor_rows != null ? (
                  F.boundary.sync_cursor_rows
                ) : (
                  <Absent kind="uninstrumented" />
                )}
              </td>
              <td className="col-muted">
                writer 가 없어 <b>기록하지 않음</b> — "pull 하지 않았다"가 아닙니다
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* 관측 범위 안내 — 측정값이 아니므로 KPI 로 세우지 않는다 */}
      <div className="card card-pad">
        <p className="t-sm m-0" style={{ fontWeight: 600 }}>
          여기까지가 Cloud 의 관측 범위입니다
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          발번 이후 — Sync Agent(DMZ) → Intake(내부망) → Screening → 최종 게시와 소비자 노출 — 은 온프렘
          영역이라 Cloud Super Admin 에서 관측하지 못합니다.{' '}
          <b>관측 불가는 0건도, 실패 판정도 아닙니다.</b> 그 구간은 증권사 관리 환경 콘솔이 답합니다.
        </p>
      </div>
    </div>
  );
}
