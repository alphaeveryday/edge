/* holdings 결손 영향 — Impact 최소판 (ALPHA-686, 판정 스펙 §6 첫 슬라이스).
 *
 * 답하는 질문: "이 결손으로 어떤 ETF·분석이 영향을 받나, 어디부터 복구하나". 데이터 장애를
 * 기술 작업에서 끝내지 않고 제품 영향까지 잇는다(멘토 §4.4).
 *
 * 주장의 한계(정직 표기): 누락 = 기대(Planner snapshot) − 이 런의 적재분. 수집/정제/적재 중
 * 어디서 탈락했는지는 단정하지 않는다(그 분해는 S3 로그 소관). "분석 없음"도 결손의 결과라고
 * 단정하지 않는다 — 트리거 미발동 정상 무분석과 구분할 수 없다.
 */
import { useSearchParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import { useHoldingsImpact } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

export function HoldingsImpactPage() {
  const [params] = useSearchParams();
  const runKey = params.get('runKey') ?? undefined;
  const { data, isPending, isError, error } = useHoldingsImpact(runKey);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={5} />;

  if (!data.runKey) {
    return (
      <div className="card">
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          원장에 기록된 시장(etf-daily) 런이 아직 없습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">KRX 구성종목(holdings) 결손 영향</span>
          {data.snapshotMissing ? (
            /* 계산 불가(UNKNOWN)는 "결손 없음"과 다르다 — 스펙 §6.3 */
            <StatusBadge tone="neutral">영향 범위 계산 불가</StatusBadge>
          ) : data.missing.length === 0 ? (
            <StatusBadge tone="active">결손 없음</StatusBadge>
          ) : (
            <StatusBadge tone="warn">누락 ETF {data.missing.length}종</StatusBadge>
          )}
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-2)' }}>
          {data.runKey} · 기준 거래일 {data.expectedAsOf ?? '—'}
          {!data.snapshotMissing && (
            <>
              {' · '}기대 ETF <b>{data.expectedCount}종</b> 중 이 런의 적재{' '}
              <b>{data.loadedCount}종</b>
            </>
          )}
        </p>
        {data.snapshotMissing && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
            이 런에는 기대 목록(expectation snapshot)이 없어 누락을 계산할 수 없습니다 —
            영향 없음이 아니라 <b>모름</b>입니다.
          </p>
        )}
      </div>

      {data.missing.length > 0 && (
        <div className="card">
          <div className="card-head">
            <span className="t-label">누락 ETF → 기준일 분석</span>
            <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
              누락 = 기대 − 이 런의 적재분 (수집/정제/적재 중 탈락 지점은 여기서 단정하지 않음)
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
                          <b>{a.publicationStatus ?? '—'}</b> · {a.summary ?? a.explanationResultId}
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
        </div>
      )}
    </div>
  );
}
