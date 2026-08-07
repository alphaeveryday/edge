/* 구성종목 결손 상세 — ETF 구성종목 데이터 계약에 특화된 결손 보고서 (ALPHA-686 → ALPHA-738).
 *
 * 답하는 질문: "이 실행에서 관측된 구성종목 결손이 지금 어떤 상태이고, 어떤 ETF 가 빠져 있으며,
 * 같은 기준일에 어떤 분석이 있었나".
 *
 * **고정 탭이 아니라 조건부 드릴다운이다.** 상태마다 전용 페이지를 만들지 않는다 — 이 화면이
 * 따로 있는 이유는 이 데이터셋에만 있는 고유 증거(누락 ETF ID·instrument 존재 여부·기대와
 * 적재의 차집합·같은 기준일 분석) 때문이지 "부분 결손"이라는 상태 때문이 아니다.
 *
 * 주장의 한계(정직 표기):
 *   · 누락 = 기대(Planner snapshot) − 기준일 적재분. 수집/정제/적재 중 어디서 탈락했는지는
 *     단정하지 않는다(그 분해는 S3 로그 소관).
 *   · 분석은 **연관**일 뿐 영향받았다고 단정하지 않는다 — 응답에 lineage 가 없다. "분석 없음"도
 *     결손의 결과라고 단정하지 않는다(트리거 미발동 정상 무분석과 구분할 수 없다).
 *   · ⚠️ **당시 결손과 현재 결손은 다른 사실이다.** 기대 목록은 그 런의 스냅샷이지만 적재
 *     상태는 **조회 시점**의 기준일 데이터다. 응답에 당시 누락 목록이 없어 복원하지 않는다.
 */
import { Link, useSearchParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import { ApiError } from '../api/client';
import type { HoldingsImpact } from '../domains/sources';
import { useHoldingsImpact } from '../domains/sources/hooks';
import { useConsoleEvaluation } from './ops/shared';
import { runHref } from './ops/investigation';
import { MOCK_HOLDINGS } from '../mock/preview';
import { MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';
import '../styles/ops.css';

/** 조사 경로 — 실제 식별자로만 만든다. 사건에서 왔으면 그 문맥을 유지한다 */
function Crumb({ runKey, incidentId }: { runKey?: string; incidentId?: string }) {
  const { incidents } = useConsoleEvaluation();
  const incident = incidentId ? incidents.find((i) => i.root.vid === incidentId) : undefined;
  return (
    <nav className="t-xs ops-crumb" aria-label="조사 경로">
      {incidentId ? (
        <>
          <Link to="/ops/incidents">문제·사건</Link>
          <span aria-hidden="true">›</span>
          {incident ? (
            <Link to={`/ops/incidents/${encodeURIComponent(incidentId)}`}>{incident.root.title}</Link>
          ) : (
            <span>사건 {incidentId} (확인되지 않음)</span>
          )}
          <span aria-hidden="true">›</span>
        </>
      ) : (
        <>
          <Link to="/ops/runs">실행</Link>
          <span aria-hidden="true">›</span>
        </>
      )}
      {runKey && (
        <>
          <Link to={runHref(runKey)} className="mono">
            {runKey}
          </Link>
          <span aria-hidden="true">›</span>
          <Link to={`/sources?runKey=${encodeURIComponent(runKey)}`}>ETF 구성종목</Link>
          <span aria-hidden="true">›</span>
        </>
      )}
      <span style={{ color: 'var(--fg-1)' }}>구성종목 결손 상세</span>
    </nav>
  );
}

export function HoldingsImpactPage() {
  const [params] = useSearchParams();
  /* **문맥 없는 최신 런 자동 선택을 쓰지 않는다.** 이 화면은 "어느 실행을 조사하는가"가
   * 정해졌을 때 여는 상세라, runKey 가 없으면 임의의 런을 골라 그 실행의 결손처럼 보이게
   * 하는 대신 어디서 들어와야 하는지 말한다. */
  const runKey = params.get('runKey')?.trim() || undefined;
  const incidentId = params.get('incident')?.trim() || undefined;
  const { data, isPending, isError, error } = useHoldingsImpact(runKey, Boolean(runKey));

  if (!runKey) {
    return (
      <div className="flex flex-col gap-4">
        <Crumb incidentId={incidentId} />
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>조사할 실행이 지정되지 않았습니다</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            구성종목 결손 상세는 특정 <code>etf-daily</code> 실행의 기대 목록을 기준으로 계산합니다 —
            실행이 정해지지 않으면 최신 런을 임의로 골라 보여주지 않습니다. 실행 상세에서 ETF
            구성종목 결손을 선택해 주세요.
          </p>
          <p className="t-xs m-0" style={{ marginTop: 8 }}>
            <Link to="/ops/runs">실행 목록으로 이동 →</Link>
          </p>
        </div>
      </div>
    );
  }

  if (isError) {
    /* 지정한 런이 없으면 **최신 런으로 대체하지 않는다** — 오타가 다른 런의 결손으로 보인다 */
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="flex flex-col gap-4">
          <Crumb runKey={runKey} incidentId={incidentId} />
          <div className="card card-pad">
            <p className="t-sm m-0" style={{ fontWeight: 600 }}>지정한 실행을 찾을 수 없습니다</p>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
              <code>{runKey}</code> 의 기대 목록이 원장에 없습니다. 다른 실행으로 대체하지
              않습니다. <Link to="/ops/runs">실행 목록으로 이동 →</Link>
            </p>
          </div>
        </div>
      );
    }
    return <LoadError error={error} />;
  }
  if (isPending) return <PageSkeleton rows={5} />;

  return (
    <div className="flex flex-col gap-4">
      <Crumb runKey={runKey} incidentId={incidentId} />
      {data.runKey === null ? (
        /* 응답이 런을 지목하지 못했다 — 목으로 화면 구조만 보인다 */
        <MockPreview>
          <HoldingsImpactBody data={MOCK_HOLDINGS} runKey={runKey} mock />
        </MockPreview>
      ) : (
        <HoldingsImpactBody data={data} runKey={runKey} />
      )}
    </div>
  );
}

function HoldingsImpactBody({
  data,
  runKey,
  mock = false,
}: {
  data: HoldingsImpact;
  runKey: string;
  mock?: boolean;
}) {
  const loadDone = !data.loadPending;

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">
            구성종목 결손 상세 {mock && <MockChip />}
          </span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            ETF 구성종목 · 현재 기준일 적재 상태 및 연관 분석
          </span>
          {data.snapshotMissing ? (
            /* 계산 불가(UNKNOWN)는 "결손 없음"과 다르다 — 스펙 §6.3 */
            <StatusBadge tone="neutral">영향 범위 계산 불가</StatusBadge>
          ) : !loadDone ? (
            /* 적재가 안 끝난 런의 차집합은 전부 "누락"으로 보인다 — 확정하지 않는다 */
            <StatusBadge tone="env">적재 미귀결 — 판정 유보</StatusBadge>
          ) : data.missing.length === 0 ? (
            <StatusBadge tone="active">결손 없음</StatusBadge>
          ) : (
            <StatusBadge tone="warn">누락 ETF {data.missing.length}종</StatusBadge>
          )}
        </div>
        {/* ⚠️ 두 사실을 섞지 않는다 — 기대 목록은 그 실행의 스냅샷이고, 적재 수는 조회 시점 값이다 */}
        <div className="ops-facts" style={{ marginTop: 8 }}>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>대상 실행</span>
            <span className="mono">{data.runKey ?? runKey}</span>
          </div>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>기준 거래일</span>
            <span>{data.expectedAsOf ?? '—'}</span>
          </div>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>현재 기준일 적재 상태</span>
            <span>
              {data.snapshotMissing
                ? '계산 불가 — 기대 목록 없음'
                : `${data.loadedCount}/${data.expectedCount}${
                    data.missing.length > 0 ? ` · 현재 누락 ${data.missing.length}종` : ''
                  }`}
            </span>
          </div>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>조회 기준</span>
            <span>{data.expectedAsOf ?? '—'} 데이터의 현재 적재 상태</span>
          </div>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>당시 누락 목록</span>
            <span style={{ color: 'var(--fg-4)' }} title="그 실행 시점의 누락 ETF 를 담는 필드가 응답에 없다 — 현재 적재 상태에서 역추정하지 않는다">
              계측 없음
            </span>
          </div>
        </div>
        {loadDone && !data.snapshotMissing && data.missing.length === 0 && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-2)', marginTop: 8 }}>
            현재 기준일 적재 상태는 <b>{data.loadedCount}/{data.expectedCount}</b> 입니다 — 이 실행에서
            결손이 관측됐더라도 이후 재실행으로 복구됐을 수 있습니다. 두 시점은 다른 사실이라 이
            화면이 하나로 합치지 않습니다.
          </p>
        )}
        {data.snapshotMissing && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
            기대 목록(expectation snapshot) 또는 기준 거래일이 없어 누락을 계산할 수 없습니다 —
            영향 없음이 아니라 <b>모름</b>입니다.
          </p>
        )}
      </div>

      {data.missing.length > 0 && (
        <div className="card">
          <div className="card-head">
            <span className="t-label">
              누락 ETF의 연관 분석{!loadDone && ' (잠정 — 적재 미귀결)'}
            </span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              누락 = 기대 − 기준일 적재분 · 같은 기준일·같은 ETF 의 분석을 잇는 것이지 결손 때문에
              영향받았다고 단정하지 않습니다(응답에 lineage 가 없습니다)
            </span>
          </div>
          <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
            <tbody>
              {data.missing.map((m) => (
                <tr key={m.ourEtfId}>
                  <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap', fontWeight: 600 }}>
                    {m.ourEtfId}
                  </td>
                  <td style={{ padding: '3px 10px 3px 0' }}>
                    {m.etfName ?? (
                      <span style={{ color: 'var(--fg-3)' }}>
                        이름 없음 — instrument 미등록(프로필 수집까지 결손)
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '3px 0' }}>
                    {m.analyses.length > 0 ? (
                      m.analyses.map((a) => (
                        <div key={a.explanationResultId} className="t-xs">
                          <b>{a.publicationStatus ?? '—'}</b> ·{' '}
                          {/* 연관 분석 → 상세로 내려가는 고리(ALPHA-692). runId 없으면(이론상
                           * 없음) 텍스트 폴백 — 링크가 죽는 것보다 낫다 */}
                          {a.explanationRunId && !mock ? (
                            <Link to={`/analyses/${encodeURIComponent(a.explanationRunId)}`}>
                              {a.summary ?? a.explanationResultId}
                            </Link>
                          ) : (
                            (a.summary ?? a.explanationResultId)
                          )}
                        </div>
                      ))
                    ) : (
                      /* "분석 없음"은 결손 결과로 단정하지 않는다 — 오귀인 방지 */
                      <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                        기준일 분석 없음 (사유 미상 — 트리거 미발동일 수 있음)
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.recommendedAction && (
            <p className="t-xs m-0" style={{ marginTop: 8, color: 'var(--fg-2)' }}>
              권장 조치(수동): {data.recommendedAction}
            </p>
          )}
          <p className="t-xs m-0" style={{ marginTop: 8 }}>
            <Link to={`/sources?runKey=${encodeURIComponent(runKey)}`}>← 실행 원장 상세로 돌아가기</Link>
          </p>
        </div>
      )}
    </div>
  );
}
