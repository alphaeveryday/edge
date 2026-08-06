import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Delta, PageSkeleton, StatusBadge, formatDelta } from 'ui-kit';
import {
  ANALYSIS_CONFIDENCE_LABEL,
  ANALYSIS_STATUS_LABEL,
  ANALYSIS_STATUS_TONE,
} from '../domains/analyses';
import { useAnalysis } from '../domains/analyses/hooks';
import { MOCK_ANALYSES } from '../mock/preview';
import { InfoPopover } from './_shared/InfoPopover';
import { MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

export function AnalysisDetailPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  /* 목 미리보기에서 온 링크는 같은 목을 읽는다 — 없는 분석을 여는 죽은 링크를 만들지 않는다 */
  const preview = params.get('preview') === 'mock';
  const live = useAnalysis(id);
  const a = preview ? MOCK_ANALYSES.find((x) => x.id === id) : live.analysis;
  const { isPending, isError, error } = live;

  if (!preview && isError) return <LoadError error={error} />;
  if (!preview && isPending) return <PageSkeleton rows={5} />;
  if (!a) {
    return (
      <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
        {/* 목록 창(최신 200건) 기반 조회라 "없는 ID"와 "창 밖의 과거 분석"을 여기서 가를 수
         * 없다 — 단정하면 유효한 과거 링크가 오타처럼 읽힌다. 단건 조회 API 는 후속. */}
        해당 분석 건을 찾을 수 없습니다 — 없는 ID 이거나, 최신 200건 목록 창 밖의 과거
        분석일 수 있습니다.
      </div>
    );
  }

  const body = (
    <div className="flex max-w-[1100px] flex-col gap-4">
      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        <Link to="/analyses">가격 변동 분석 목록</Link>
        <span aria-hidden="true">›</span>
        <Link to={`/analyses/symbol/${a.market}/${a.code}${preview ? '?preview=mock' : ''}`}>
          {a.name} {a.code}
        </Link>
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>기준 {a.basisTime} 분석</span>
      </nav>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="t-h1">{a.name}</span>
        {preview && <MockChip />}
        <span className="mono" style={{ color: 'var(--fg-3)' }}>{a.code}</span>
        <span className="tag">{a.market}</span>
        <Delta direction={a.direction} pct={a.changePct} style={{ fontSize: 16 }} />
        <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>{ANALYSIS_STATUS_LABEL[a.status]}</StatusBadge>
        {/* 게시 수명주기는 전달 경계 축이라 이 화면의 범위 밖이다 — 표시하지 않는다(ADR-0026).
         * 타입·API 필드는 그대로 두고 노출만 뺀다. */}
      </div>

      <div className="grid items-start gap-4" style={{ gridTemplateColumns: '1fr 340px' }}>
        {/* 좌측 — 근거·분석 결과 */}
        <div className="flex min-w-0 flex-col gap-4">
          <div className="card">
            <div className="card-head">
              <span className="t-label">사용 근거</span>
              {/* 표시 상한에 잘렸으면 총 건수와 표시 건수를 같이 말한다 — 건수만 줄여 쓰면
                  근거가 그것뿐인 것으로 읽힌다. UI 와 API 는 따로 배포돼 총 건수를 아직 안 주는
                  응답을 만날 수 있다 — 그땐 종전대로 표시 건수를 말한다(빈 "건" 방지) */}
              <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
                {a.evidenceTotal ?? a.evidence.length}건
                {(a.evidenceTotal ?? 0) > a.evidence.length &&
                  ` · 상위 ${a.evidence.length}건 표시`}
              </span>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                응답이 주는 축만 — 구분 · 제목 · 수집 소스 · 발행 시각
                <InfoPopover
                  label="사용 근거"
                  title="사용 근거"
                  text={
                    '이 분석이 참고한 근거 문서 목록이다. 응답이 주는 축은 네 개뿐이다 —\n' +
                    '구분(뉴스·공시) · 문서 제목 · 수집 소스 · 발행 시각.\n\n' +
                    '⚠️ assertion 내용 · source event · 단계별 사용 관계 · 우선순위 · 가중치 ·\n' +
                    '가격 관측 근거 · 원문 링크는 이 응답에 없다 — 그래서 계보 단계로 그리지 않는다.\n' +
                    '표시 상한이 있어 총 건수와 표시 건수를 함께 낸다.'
                  }
                />
              </span>
            </div>
            <div className="flex flex-col">
              {/* 근거가 없으면 가짜 링크를 만들지 않는다 — 이 분석 결과의 계보가 없다는 사실이다 */}
              {a.evidence.length === 0 && (
                <p className="t-xs m-0 px-4 py-3" style={{ color: 'var(--fg-3)' }}>
                  연결된 사용 근거가 없습니다 — 전체 뉴스 목록을 대신 보여주지 않습니다(선택한
                  결과와 무관한 문서가 근거처럼 읽힙니다).
                </p>
              )}
              {a.evidence.map((e, i) => (
                <div
                  key={i}
                  className="flex items-start gap-3 px-4 py-3"
                  style={{ borderBottom: '1px solid var(--border-faint)' }}
                >
                  <span className="chip mt-px flex-none">{e.type}</span>
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="t-body" style={{ fontWeight: 500 }}>{e.title}</span>
                    <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
                      {e.source} · {e.time}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="t-label">분석 결과</span>
              {a.confidence && (
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  신뢰도 <span className="num">{ANALYSIS_CONFIDENCE_LABEL[a.confidence]}</span>
                </span>
              )}
            </div>
            <div className="p-4">
              <p
                className="t-body m-0 whitespace-pre-line"
                style={{ color: 'var(--fg-2)', lineHeight: 1.6 }}
              >
                {a.result}
              </p>
            </div>
          </div>
        </div>

        {/* 우측 — 정보·액션 */}
        <div className="flex flex-col gap-4">
          <div className="card">
            <div className="card-head">
              <span className="t-label">종목 / 등락 정보</span>
            </div>
            <div className="flex flex-col gap-3 p-4">
              <div className="flex justify-between gap-3">
                <span className="t-sm" style={{ color: 'var(--fg-3)' }}>종목</span>
                <span className="t-sm text-right font-semibold">
                  {a.name} <span className="mono" style={{ fontWeight: 400, color: 'var(--fg-3)' }}>{a.code}</span>
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="t-sm" style={{ color: 'var(--fg-3)' }}>시장</span>
                <span className="t-sm num">{a.market}</span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="t-sm" style={{ color: 'var(--fg-3)' }}>방향</span>
                <span className={`delta ${a.direction > 0 ? 'delta-up' : 'delta-down'}`} style={{ fontSize: 12 }}>
                  {a.direction > 0 ? '▲ 상승' : '▼ 하락'}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="t-sm" style={{ color: 'var(--fg-3)' }}>등락률</span>
                <span className={`delta ${a.direction > 0 ? 'delta-up' : 'delta-down'}`} style={{ fontSize: 12 }}>
                  {formatDelta(a.direction, a.changePct)}
                </span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="t-label">분석 정보</span>
            </div>
            <div className="flex flex-col gap-3 p-4">
              <div className="flex flex-col gap-0.5">
                <span className="t-label">변동 기준 시각</span>
                <span className="t-sm num">{a.basisTimeAbs}</span>
              </div>
              <hr className="hr" />
              <div className="flex flex-col gap-0.5">
                <span className="t-label">분석 완료 시각</span>
                <span className="t-sm num">{a.doneTime}</span>
              </div>
            </div>
          </div>

          {/* 게시 무효화(ALPHA-440)는 게시본을 내리고 테넌트에 전파하는 **전달 축** 액션이라
           * 이 화면의 범위 밖이다 — UI 노출만 뺐고 mutation·API 는 그대로 살아 있다. */}
        </div>
      </div>
    </div>
  );

  return preview ? <MockPreview>{body}</MockPreview> : body;
}
