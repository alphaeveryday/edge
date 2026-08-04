/* 실행 축 — 런과 작업 (ALPHA-738).
 *
 * 실행 방식은 정규 / 수동 / 백필 셋이다. 백필은 흐름이 아니라 실행 방식이고, 같은 체인을
 * 과거 날짜로 다시 돌린다 — 산출 축 숫자에 "어느 런이 만든 것인가"가 붙어야 하는 이유다.
 */
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { retryCap } from '../../rules/rules';
import { Absent, AxisHeader, F, Info, fmt, kst, useFocusRow } from './shared';
import '../../styles/ops.css';

const KIND_LABEL: Record<string, string> = { scheduled: '정규', manual: '수동', backfill: '백필' };

const LEDGER_TONE: Record<string, BadgeTone> = {
  SUCCEEDED: 'active',
  FAILED: 'blocked',
  TIMED_OUT: 'blocked',
  ABORTED: 'blocked',
  RUNNING: 'warn',
};
const OUTCOME_TONE: Record<string, BadgeTone> = {
  FULFILLED: 'active',
  FAILED: 'blocked',
  BLOCKED: 'warn',
  PENDING: 'neutral',
};
const DATA_TONE: Record<string, BadgeTone> = {
  VALID: 'active',
  INCOMPLETE: 'warn',
  INVALID: 'blocked',
  UNKNOWN: 'neutral',
};

const COMPLETENESS_TIP =
  '완전성 = received / expected(엔티티 기준). 분모가 없는 작업은 위반이 아니라 평가 대상이 아니다 — ' +
  '분모를 |유니버스| × 거래일 곱으로 잡으면 휴장일마다 거짓 INCOMPLETE 가 난다.';
const ATTEMPT_TIP =
  '시도 / 정책 상한. 상한은 CatalogEntry 에 아직 없어 목값이다(SFN Retry 블록 0개) — ' +
  '분모 없이 2/3 처럼 쓰면 안 되고, 계측이 붙기 전엔 "시도 N회"까지만 정직하다.';
const FAILED_TIP =
  'ops.failed_records — 스텝이 스스로 판정한 유실값이며 skipped_* 를 더한 값이 아니다. 잡마다 단위가 다르다.';

export function RunAxisPage() {
  useFocusRow();
  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="오늘 어떤 런이 돌았고, 그 안의 작업은 귀결됐는가?" />

      <div className="card">
        <div className="card-head">
          <span className="t-label">런</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            원장과 AWS 제어면이 어긋나면 어느 쪽으로도 덮지 않고 둘 다 보여줍니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>런</th>
              <th>실행 방식</th>
              <th>거래일</th>
              <th>원장</th>
              <th>AWS 제어면</th>
              <th>마감</th>
            </tr>
          </thead>
          <tbody>
            {F.runs.map((r) => (
              <tr key={r.id} id={'run-' + r.id}>
                <td className="mono">
                  {r.id}
                  {r.mock && (
                    <span className="chip" style={{ marginLeft: 6 }}>
                      MOCK
                    </span>
                  )}
                </td>
                <td>
                  <span className="chip">{KIND_LABEL[r.kind] ?? r.kind}</span>
                </td>
                <td className="col-muted">{r.trading_date}</td>
                <td>
                  {r.no_run_row ? (
                    <StatusBadge tone="blocked">행 없음</StatusBadge>
                  ) : r.ledger_status ? (
                    <StatusBadge tone={LEDGER_TONE[r.ledger_status] ?? 'neutral'}>{r.ledger_status}</StatusBadge>
                  ) : (
                    <Absent kind="none" />
                  )}
                </td>
                <td>
                  {r.aws_status ? (
                    <StatusBadge tone={r.aws_status === 'SUCCEEDED' ? 'active' : 'blocked'}>
                      {r.aws_status}
                    </StatusBadge>
                  ) : (
                    <Absent kind="none" />
                  )}
                </td>
                <td className="col-muted">{r.deadline ? kst(r.deadline) : <Absent kind="none" />}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            백필은 흐름이 아니라 실행 방식입니다 — 같은 체인을 과거 날짜로 다시 돌립니다. 실행 방식(kind)은 아직 원장에
            기록되지 않아 이 열은 목값입니다.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">작업 {F.tasks.length}건</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            열 제목의 ⓘ 에 단위·분모·주의가 있습니다
          </span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>작업</th>
                <th>스테이지</th>
                <th>귀결</th>
                <th className="num">산출 행</th>
                <th className="num">
                  유실 <Info tip={FAILED_TIP} />
                </th>
                <th>데이터 판정</th>
                <th>
                  완전성 <Info tip={COMPLETENESS_TIP} />
                </th>
                <th className="num">
                  시도 <Info tip={ATTEMPT_TIP} />
                </th>
              </tr>
            </thead>
            <tbody>
              {F.tasks.map((t) => (
                <tr key={t.task_key} id={'task-' + t.task_key}>
                  <td className="mono">{t.task_key}</td>
                  <td className="col-muted">{t.stage}</td>
                  <td>
                    <StatusBadge tone={OUTCOME_TONE[t.task_outcome] ?? 'neutral'}>{t.task_outcome}</StatusBadge>
                  </td>
                  <td className="num">{fmt(t.records_out)}</td>
                  <td className="num" style={t.failed_records ? { color: 'var(--warn)', fontWeight: 600 } : undefined}>
                    {t.failed_records != null ? fmt(t.failed_records) : <Absent kind="none" />}
                  </td>
                  <td>
                    {t.data_status ? (
                      <StatusBadge tone={DATA_TONE[t.data_status] ?? 'neutral'}>{t.data_status}</StatusBadge>
                    ) : (
                      <Absent kind="none" />
                    )}
                  </td>
                  <td>
                    {t.completeness_expected != null ? (
                      <>
                        <span className="num">
                          {t.completeness_received}/{t.completeness_expected}
                        </span>
                        {t.cmpl_mock && (
                          <span className="chip" style={{ marginLeft: 6 }}>
                            MOCK
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="t-xs" style={{ color: 'var(--fg-4)' }} title="분모가 배선되지 않아 평가 대상이 아니다 — 결손 0이라는 뜻이 아니다">
                        분모 없음
                      </span>
                    )}
                  </td>
                  {/* 정책 상한이 없으면 분모를 지어내지 않는다 — "시도 N회"까지만 정직하다 */}
                  <td className="num col-muted">
                    {retryCap(t) != null ? (
                      <>
                        <span className="num">
                          {t.attempts}/{retryCap(t)}
                        </span>
                        {t.retry_mock && (
                          <span className="chip" style={{ marginLeft: 6 }}>
                            MOCK
                          </span>
                        )}
                      </>
                    ) : (
                      <span title="재시도 정책 상한이 선언돼 있지 않다(SFN Retry 블록 0개) — 분모가 없어 소진 여부를 말할 수 없다">
                        {t.attempts != null ? `${t.attempts}회` : <Absent kind="none" />}
                        <span className="chip" style={{ marginLeft: 6 }}>
                          상한 미선언
                        </span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
