/* 데이터셋 신선도 — 여기가 진짜 배치다 (ALPHA-738).
 *
 * 데이터셋마다 수집→정제→적재가 돌고 주기도 다르다. 앞 화면의 체인(트리거→게시)은 배치가 아니라
 * 설명 생산 흐름이고, 이 데이터셋들이 그 재료다.
 */
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { Absent, AxisHeader, F, Info, kst, useFocusRow } from './shared';
import '../../styles/ops.css';

/** 신선도 판정 — 근거가 없으면 FRESH 라고 말하지 않는다 */
function freshness(d: (typeof F.datasets)[number]): { label: string; tone: BadgeTone; tip: string } {
  if (d.unverifiable)
    return { label: '판정 불가', tone: 'neutral', tip: d.unverifiable };
  if (!d.contract)
    return { label: '계약 없음', tone: 'neutral', tip: 'DatasetContract 등록이 없어 기대 기준일 자체가 없다' };
  if (d.window_contract)
    return { label: '창 계약', tone: 'warn', tip: '기준일이 아니라 창으로 계약된 데이터셋 — as-of 비교 대상이 아니다' };
  if (d.actual_as_of != null && d.expected_as_of != null && d.actual_as_of < d.expected_as_of)
    return { label: 'STALE', tone: 'blocked', tip: '분석이 오래된 스냅샷 위에서 돈다' };
  return { label: 'FRESH', tone: 'active', tip: 'actual_as_of 가 기대 기준일 이상' };
}

export function DatasetPage() {
  useFocusRow();
  const byDataset = new Map<string, typeof F.tasks>();
  for (const t of F.tasks) {
    const key = t.dataset ?? '—';
    if (!byDataset.has(key)) byDataset.set(key, []);
    byDataset.get(key)!.push(t);
  }

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="각 데이터셋이 언제 기준의 사실을 담고 있는가?" />

      <div className="card">
        <div className="card-head">
          <span className="t-label">신선도</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            기대 기준일 대비 실제 기준일
            <Info
              tip={
                'expected_as_of = 계약이 기대하는 기준일, actual_as_of = 데이터가 실제로 담고 있는 기준일.\n' +
                '수집 시각(collected_at)과는 다른 축이다 — 오늘 수집해도 담긴 기준일은 어제일 수 있다.'
              }
              label="기준일 축"
            />
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>데이터셋</th>
              <th>기대 기준일</th>
              <th>실제 기준일</th>
              <th>수집 시각</th>
              <th>판정</th>
              <th>다음 실행</th>
            </tr>
          </thead>
          <tbody>
            {F.datasets.map((d) => {
              const f = freshness(d);
              return (
                <tr key={d.id} id={'ds-' + d.id}>
                  <td className="mono">
                    {d.id}
                    {d.mock && (
                      <span className="chip" style={{ marginLeft: 6 }}>
                        MOCK
                      </span>
                    )}
                  </td>
                  <td>{d.expected_as_of ?? <Absent kind="none" />}</td>
                  <td>
                    {d.actual_as_of ?? (
                      <span className="t-xs" style={{ color: 'var(--fg-4)' }} title="actual_as_of writer 가 없어 근거 자체가 없다 — 오래됐다는 뜻이 아니다">
                        근거 없음
                      </span>
                    )}
                  </td>
                  <td className="col-muted">{d.collected_at ? kst(d.collected_at) : <Absent kind="none" />}</td>
                  <td>
                    <span title={f.tip}>
                      <StatusBadge tone={f.tone}>{f.label}</StatusBadge>
                    </span>
                  </td>
                  <td className="col-muted">{d.next_run ?? <Absent kind="none" />}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            실제로는 계약 등록이 1건(KRX holdings)뿐이고 그마저 actual 근거가 없어 영구 UNKNOWN 입니다 — 위 실제 기준일은
            목값입니다. 이 축이 배선돼야 R08(STALE)이 실제로 돕니다. 다만 KRX holdings 는 원천이 기준일을 주지 않아{' '}
            <b>영구 검증 불가</b>이며, UNKNOWN 유지가 설계 결정입니다.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">수집 → 정제 → 적재</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            데이터셋별 배치 흐름 — 여기가 진짜 배치입니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>데이터셋</th>
              <th>작업</th>
              <th>결과</th>
            </tr>
          </thead>
          <tbody>
            {[...byDataset.entries()].map(([d, xs]) => (
              <tr key={d}>
                <td className="mono">{d}</td>
                <td className="col-muted t-xs">{xs.map((x) => x.task_key).join(' → ')}</td>
                <td>
                  {xs.every((x) => x.task_outcome === 'FULFILLED') ? (
                    <StatusBadge tone="active">전건 귀결</StatusBadge>
                  ) : (
                    <StatusBadge tone="blocked">미귀결 포함</StatusBadge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
