/* 종목 상세 — 최신 유효 설명과 오늘의 분석 이력 (ALPHA-738).
 *
 * 목록이 종목당 한 행으로 접히므로, 그 종목의 **읽을 설명 하나**와 **오늘 무슨 시도가 있었나**를
 * 여기서 편다.
 *
 * 지키는 선:
 *   · 최신 유효 설명은 **변동 기준 시각** 기준이다 — 늦게 끝난 과거 기준이 덮지 않는다.
 *   · 최신 시도가 실패해도 이전 유효 설명을 지우지 않는다. 실패한 시도를 누르면 분석 결과가
 *     아니라 **관련 실행·문제**로 보낸다(없는 결과를 여는 죽은 링크를 만들지 않는다).
 *   · 게시·발번 등 전달 축은 이 화면의 범위 밖이라 표시하지 않는다.
 */
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Delta, PageSkeleton, StatusBadge } from 'ui-kit';
import { ANALYSIS_CONFIDENCE_LABEL, ANALYSIS_STATUS_LABEL, ANALYSIS_STATUS_TONE } from '../domains/analyses';
import { useAnalyses } from '../domains/analyses/hooks';
import { findGroup, hasResult } from '../domains/analyses/symbols';
import { MOCK_ANALYSES } from '../mock/preview';
import { MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

export function AnalysisSymbolPage() {
  const { market = '', code = '' } = useParams();
  const [params] = useSearchParams();
  const preview = params.get('preview') === 'mock';
  const query = useAnalyses();

  if (!preview && query.isError) return <LoadError error={query.error} />;
  if (!preview && query.isPending) return <PageSkeleton rows={6} />;

  const items = preview ? MOCK_ANALYSES : (query.data ?? []);
  const group = findGroup(items, market, code);

  if (!group) {
    return (
      <div className="card card-pad">
        <p className="t-sm m-0">이 종목의 분석이 없습니다.</p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          <span className="mono">
            {market} {code}
          </span>{' '}
          의 분석 결과가 조회되지 않았습니다 — 다른 종목으로 대체하지 않습니다.{' '}
          <Link to="/analyses">가격 변동 분석 목록으로</Link>
        </p>
      </div>
    );
  }

  const { latestValid, latestAttempt } = group;
  const detail = (id: string) => (preview ? `/analyses/${id}?preview=mock` : `/analyses/${id}`);

  const body = (
    <div className="flex max-w-[900px] flex-col gap-4">
      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        <Link to={preview ? '/analyses' : '/analyses'}>가격 변동 분석 목록</Link>
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>
          {group.name} {group.code}
        </span>
      </nav>

      <div className="card">
        <div className="card-head">
          <span className="t-h3">{group.name}</span>
          <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
            {group.code}
          </span>
          <span className="tag">{group.market}</span>
          {preview && <MockChip />}
          <span className="t-xs" style={{ color: 'var(--fg-3)', marginLeft: 'auto' }}>
            오늘 분석 {group.todayCount}건
          </span>
        </div>
        <div className="card-pad">
          {latestValid ? (
            <>
              <span className="t-label">최신 유효 설명</span>
              <p className="t-body m-0" style={{ marginTop: 6 }}>
                {latestValid.result}
              </p>
              <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
                기준 시각 <b>{latestValid.basisTime}</b> · 완료 {latestValid.doneTime} ·{' '}
                <Delta direction={latestValid.direction} pct={latestValid.changePct} />
                {latestValid.confidence && ` · 신뢰도 ${ANALYSIS_CONFIDENCE_LABEL[latestValid.confidence]}`}
                {' · '}
                사용 근거 {latestValid.evidenceTotal ?? latestValid.evidence.length}건
              </p>
              <p className="t-xs m-0" style={{ marginTop: 8 }}>
                <Link to={detail(latestValid.id)}>이 분석 상세 보기 →</Link>
              </p>
            </>
          ) : (
            <>
              <span className="t-label">최신 유효 설명</span>
              <p className="t-sm m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
                아직 유효한 설명이 없습니다 — 오늘의 시도는 아래 이력에서 확인합니다.
              </p>
            </>
          )}

          {/* 최신 시도가 유효 결과가 아니면 그 사실을 따로 말한다(설명을 덮지 않는다) */}
          {group.attemptPending && (
            <p className="t-xs m-0" style={{ color: 'var(--fg-2)', marginTop: 10 }}>
              최근 생성 시도 <b>{latestAttempt.basisTime}</b>{' '}
              <StatusBadge tone={ANALYSIS_STATUS_TONE[latestAttempt.status]}>
                {ANALYSIS_STATUS_LABEL[latestAttempt.status]}
              </StatusBadge>{' '}
              — 위 설명은 그 이전 기준의 유효 결과입니다.
            </p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">오늘의 분석 이력 {group.todayCount}건</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            변동 기준 시각 최신순 · 실패한 시도는 관련 실행으로 갑니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>기준 시각</th>
              <th className="col-num">등락률</th>
              <th>상태</th>
              <th>완료</th>
              <th className="col-num">사용 근거</th>
              <th>상세</th>
            </tr>
          </thead>
          <tbody>
            {group.analyses.map((a) => (
              <tr key={a.id}>
                <td className="num">{a.basisTime}</td>
                <td className="col-num">
                  <Delta direction={a.direction} pct={a.changePct} />
                </td>
                <td>
                  <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>
                    {ANALYSIS_STATUS_LABEL[a.status]}
                  </StatusBadge>
                </td>
                <td className="col-muted num">{a.doneTime}</td>
                <td className="col-num">
                  {hasResult(a) ? (a.evidenceTotal ?? a.evidence.length) : '—'}
                </td>
                <td>
                  {hasResult(a) ? (
                    <Link to={detail(a.id)}>분석 상세 →</Link>
                  ) : (
                    /* 결과가 없는 시도는 분석 상세가 아니라 실행·문제가 답한다 */
                    <Link to="/ops/incidents">관련 실행·문제 →</Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );

  return preview ? <MockPreview>{body}</MockPreview> : body;
}
