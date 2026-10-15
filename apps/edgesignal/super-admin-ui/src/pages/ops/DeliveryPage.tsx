/* Cloud 게시·발번 경계 — Cloud 게시와 tenant_delivery 발번이 맞아떨어지는가 (ALPHA-738).
 *
 * 책임 경계(ADR-0026): Cloud 는 Event Store 적재·게시까지, 발번 이후는 온프렘 영역이다.
 * 소비자 수신은 측정된 지표가 아니라 **관측 범위 밖**이라 KPI 로 세우지 않고 안내로만 남긴다 —
 * 숫자 자리에 두면 0건·실패와 구분되지 않는다.
 */
import { Absent, AxisHeader, ConsoleGate, fmt, useConsoleFactsQuery, useFocusRow } from './shared';
import { axisOf } from './consoleFacts';
import { FETCH_LABEL, fetchTip } from './notRun';
import { useTenants } from '../../domains/tenants/hooks';
import '../../styles/ops.css';

export function DeliveryPage() {
  /* 테넌트 축은 `facts` 에 없다 — 발번 원장과 다른 표면이라 그쪽에서 직접 읽는다.
   * 손으로 박은 수(`0 (+시드 1)`)를 두면 실 응답이 붙는 날 거짓이 된다. */
  const tenants = useTenants();
  const q = useConsoleFactsQuery();
  useFocusRow(q.ready);
  if (!q.ready) return <ConsoleGate q={q} />;
  const b = q.facts.boundary;
  /* 경계가 관측 불가인 0 은 정합이 아니다(ALPHA-1293) — 0 으로 그리지 않는다. 0 이 아닌 수는 옛 설명
   * 엔진 분의 실측이라 그대로 그린다. ⚠️ 관측 불가는 옛 API 에서만 온다 — 새 API 는 새 분석 엔진의
   * 게시를 세서 그 표시가 없다(ALPHA-1326). */
  const boundaryCount = (n: number) => (b.blind && n === 0 ? <Absent kind="blind" /> : n);
  /* 🔴 `tenants.data` 존재만 보면 **stale 을 현재 값으로** 그린다 — react-query 는 에러에도
   * 직전 데이터를 남기므로, 값이 있다는 것이 "이번에 읽었다"를 뜻하지 않는다. 사실 축과 같은
   * 판별자(`axisOf`)를 쓴다: 여기만 다른 관용구를 쓰면 한쪽만 고쳐진다. */
  const tenantFetch = axisOf(tenants.data != null, tenants.isError);
  const production = tenants.data?.filter((t) => t.env === 'Production').length ?? null;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader
        q={q}
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
          <div className="kpi-value">
            {b.blind && b.published_without_delivery === 0 ? <Absent kind="blind" /> : fmt(b.published_without_delivery)}
          </div>
          <div className="kpi-sub">0이어야 함</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">운영 테넌트</div>
          {/* 이 수는 테넌트 원장(`/tenants`)이 답한다 — 조회 상태를 값으로 접지 않는다.
              `0` 으로 그리면 "운영 테넌트가 없다"가 되는데, 못 읽은 것과 다른 사실이다. */}
          <div className="kpi-value">
            {tenantFetch === 'loaded' ? (
              production
            ) : (
              <span className="t-sm" style={{ color: 'var(--fg-3)' }} title={fetchTip('테넌트 원장 조회(/tenants)', tenantFetch)}>
                {FETCH_LABEL[tenantFetch]}
              </span>
            )}
          </div>
          <div className="kpi-sub">
            env=Production 인 테넌트 수 — <b>기대 fan-out 분모가 아닙니다</b>(아래 참조)
            {/* `stale` 은 값을 지우지 않고 **낡았다는 사실을 옆에 둔다** — 직전 응답은
                실측이었고, 그걸 버리면 아는 것까지 잃는다. */}
            {tenantFetch === 'stale' && production !== null && (
              <>
                {' · '}
                <b style={{ color: 'var(--warn)' }}>직전 응답 {production} (마지막 조회 실패)</b>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="card" id="b-dlv">
        <div className="card-head">
          <span className="t-label">경계 정합 (R14)</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            새 분석 엔진은 가격변동 분석을 게시한 뒤 같은 작업 안에서 테넌트에 전달합니다. 전망은
            전달하지 않습니다
          </span>
        </div>
        {b.blind && (
          <p className="card-pad t-xs m-0" style={{ color: 'var(--warn)', paddingBottom: 0 }}>
            이 응답은 옛 설명 엔진의 게시만 발번과 맞댄 값입니다(새 분석 엔진을 세기 전의 API). 그
            엔진이 멈춰 있어 새 분석 엔진의 게시·발번은 여기서 보지 못합니다 — 0건이 아니라 관측
            불가입니다.
          </p>
        )}
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
              <td className="num">{boundaryCount(b.published_without_delivery)}</td>
              <td className="col-muted">
                설계상 건너뛴 것(같은 ETF 의 더 늦은 분석이 이미 전달됨, 직전 게시를 그대로 물려받음)과
                전달 대기 중인 것(단일 분석 워크플로의 시간 제한 안)은 뺍니다
              </td>
            </tr>
            <tr>
              {/* 🔴 SEED 칩을 안 단다. 이 수는 **합계**이고 `seed_note` 는 그중 일부를 설명하는
                  문장일 뿐이라, 칩은 조건부로 달아도 합계 전체를 시드라고 주장한다 — 규칙에서
                  같은 이유로 `seed` 표시를 뺀 것과 같은 판단이다(안 맞추면 사건 목록은 P0 인데
                  이 표는 "로컬 시드"라고 말한다). 기록은 아래 해석 칸이 그대로 나른다. */}
              <td>발번됐는데 현재 Cloud 비게시</td>
              <td className="num" style={{ color: 'var(--warn)' }}>
                {boundaryCount(b.delivery_now_nonpublished)}
              </td>
              <td className="col-muted">
                {b.seed_note ?? '회수 통지가 안 간 발번만 테넌트별로 셉니다. 전달된 적 없는 분석의 회수는 통지 대상이 아닙니다'}
              </td>
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
