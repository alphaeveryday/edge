/* 전달 경계 — 게시와 발번이 맞아떨어지는가 (ALPHA-738).
 *
 * 전달 이후(온프렘 심사 → 게시 → 소비자 노출)는 관측 경계 밖이다 — "전달 완료"가 곧 "읽혔다"가 아니다.
 */
import { Absent, AxisHeader, F, Info, fmt, useFocusRow } from './shared';
import '../../styles/ops.css';

export function DeliveryPage() {
  useFocusRow();
  const cov = F.delivery.coverage_0803;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="게시한 설명이 테넌트로 빠짐없이 발번됐는가?" />

      <div className="ops-cards">
        <div className="kpi">
          <div className="kpi-label">전달 행</div>
          <div className="kpi-value">{fmt(F.delivery.integrity_0803.delivery_rows)}</div>
          <div className="kpi-sub">tenant_delivery 누적</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">게시·미발번</div>
          <div className="kpi-value">{fmt(cov.published_without_new_delivery)}</div>
          <div className="kpi-sub">구조상 0이어야 함</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">소비자 수신</div>
          <div className="kpi-value" style={{ fontSize: 15, color: 'var(--fg-4)' }}>
            관측 불가
          </div>
          <div className="kpi-sub">
            접근 채널 없음 <Info tip="온프렘이 무엇을 읽었는지 확인할 채널이 클라우드에 없다 — 0이 아니다." label="소비자 수신" />
          </div>
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
              <td>게시됐는데 미발번</td>
              <td className="num">{cov.published_without_new_delivery}</td>
              <td className="col-muted">같은 트랜잭션이라 구조상 0</td>
            </tr>
            <tr>
              <td>
                전달됐는데 현재 비게시 <span className="chip">SEED</span>
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
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            전달 이후는 관측 경계 밖입니다 — 온프렘 심사·게시·소비자 노출은 증권사 관리 환경 콘솔이 답합니다.
          </p>
        </div>
      </div>
    </div>
  );
}
