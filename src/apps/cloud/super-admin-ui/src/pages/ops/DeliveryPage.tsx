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
  const b = F.boundary;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader
        question="Cloud 게시된 설명이 테넌트로 빠짐없이 발번됐는가?"
        note="Cloud 게시 → tenant_delivery 발번 구간만 답합니다"
      />

      <div className="ops-cards">
        <div className="kpi">
          <div className="kpi-label">전달 행</div>
          <div className="kpi-value">
            {b.delivery_rows != null ? fmt(b.delivery_rows) : <Absent kind="uninstrumented" />}
          </div>
          <div className="kpi-sub">tenant_delivery 누적</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Cloud 게시·미발번</div>
          <div className="kpi-value">{fmt(b.published_without_delivery)}</div>
          <div className="kpi-sub">구조상 0이어야 함</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">운영 테넌트</div>
          <div className="kpi-value">
            0 <span className="t-xs" style={{ fontWeight: 500, color: 'var(--fg-3)' }}>(+시드 1)</span>
          </div>
          <div className="kpi-sub">
            <span className="chip">SEED</span> 로컬 시드 — 기대 fan-out 분모가 아닙니다
          </div>
        </div>
      </div>

      <div className="card" id="b-dlv">
        <div className="card-head">
          <span className="t-label">경계 정합 (R14)</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            Cloud 게시와 테넌트 발번은 같은 트랜잭션이라 어긋나면 구조 문제입니다
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
              <td className="num">{b.published_without_delivery}</td>
              <td className="col-muted">같은 트랜잭션이라 구조상 0</td>
            </tr>
            <tr>
              {/* 🔴 SEED 칩을 안 단다. 이 수는 **합계**이고 `seed_note` 는 그중 일부를 설명하는
                  문장일 뿐이라, 칩은 조건부로 달아도 합계 전체를 시드라고 주장한다 — 규칙에서
                  같은 이유로 `seed` 표시를 뺀 것과 같은 판단이다(안 맞추면 사건 목록은 P0 인데
                  이 표는 "로컬 시드"라고 말한다). 기록은 아래 해석 칸이 그대로 나른다. */}
              <td>발번됐는데 현재 Cloud 비게시</td>
              <td className="num" style={{ color: 'var(--warn)' }}>
                {b.delivery_now_nonpublished}
              </td>
              <td className="col-muted">{b.seed_note ?? '무효화 통지가 안 간 발번만 셉니다'}</td>
            </tr>
            <tr>
              <td>소비 커서 행</td>
              <td className="num">
                {b.sync_cursor_rows != null ? (
                  b.sync_cursor_rows
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

      {/* 조치 단위의 한계 — 이 화면은 전역 집계라 "누구에게 안 갔나"를 답하지 못한다 */}
      <div className="card card-pad">
        <p className="t-sm m-0" style={{ fontWeight: 600 }}>
          이 화면은 전역 정합 요약입니다 — 조치 단위는 테넌트입니다
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          발번 원장의 grain 은 <code>(tenant_id, cursor)</code> 인 테넌트별 단조증가 outbox 이고, 지금 fan-out 은
          테넌트 전 행에 무차별 발번합니다 — <b>테넌트별 배정 개념이 없습니다.</b> 그래서 기대 대상 테넌트와
          누락 테넌트 목록을 낼 데이터가 현재 없고, 위 집계가 맞는다고 해서{' '}
          <b>모든 테넌트에 정상 전달됐다고 단정할 수 없습니다.</b> 테넌트별 전달 상세는 후속 구현 대상입니다.
        </p>
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
