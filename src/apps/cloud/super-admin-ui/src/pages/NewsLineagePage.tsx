/* 뉴스 계보 — Dataset Explorer 첫 슬라이스 (ALPHA-685, 지표 산출 명세화 ALPHA-697).
 *
 * 존재 이유: "표시된 집계값을 목록으로 검증할 길"(멘토: "4천 건은 어디 있어"). 그래서 이
 * 화면의 모든 숫자는 (i)에 산출 정의를 달고, 타일 클릭이 그 부분집합 목록으로 내려간다 —
 * 목록 없는 집계를 만들지 않는다. 비율은 단독 표시하지 않고 항상 N/M 을 병기한다(분모 없는
 * 퍼센트는 오독의 통로). 비율 계산은 서버가 내린 두 카운트의 산술 표현일 뿐 판정이 아니다.
 *
 * 주장의 한계(정직 표기): 원장(RDS)이 아는 건 문서의 존재 → 구조화 증거(assertion) →
 * 분석 사용까지다. "증거 없음"은 NO_EVENT·추출 실패·미실행이 구분 없이 섞인 한 통이다 —
 * 그 (i) 툴팁이 이 한계를 말한다(승격은 후속 티켓).
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageSkeleton } from 'ui-kit';
import type { NewsLineageDocument, NewsLineageStage } from '../domains/sources';
import { useNewsLineage } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

const fmt = (iso: string | null) =>
  iso ? `${new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}` : '—';

/** 분모 0 이면 '—' — 0/0 을 0% 로 표기하면 "확인했고 정상"으로 읽힌다. */
const pct = (n: number, m: number) => (m > 0 ? `${((n / m) * 100).toFixed(1)}%` : '—');

function DocumentRow({ d }: { d: NewsLineageDocument }) {
  return (
    <tr>
      <td style={{ padding: '3px 10px 3px 0', maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.title ?? ''}>
        {d.sourceUri ? (
          <a href={d.sourceUri} target="_blank" rel="noreferrer">{d.title ?? d.sourceUri}</a>
        ) : (
          d.title ?? '—'
        )}
      </td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{d.publisher ?? '—'}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{d.sourceCode ?? '—'}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{fmt(d.publishedAt)}</td>
      <td style={{ padding: '3px 10px 3px 0', whiteSpace: 'nowrap' }}>{fmt(d.availableAt)}</td>
      <td style={{ padding: '3px 10px 3px 0', textAlign: 'right' }}>
        {d.assertionCount > 0 ? `${d.assertionCount}건` : (
          /* 0 이 아니라 "없음" — NO_EVENT·실패·미실행을 여기서 가를 수 없다는 표기다 */
          <span style={{ color: 'var(--fg-3)' }}>없음</span>
        )}
      </td>
      <td style={{ padding: '3px 0', textAlign: 'center' }}>
        {d.usedInAnalysis ? '사용' : <span style={{ color: 'var(--fg-3)' }}>—</span>}
      </td>
    </tr>
  );
}

/** funnel 타일 — 클릭=그 단계 목록 필터, (i)=산출 정의(분자·분모·한계). */
function StageTile({ label, value, info, active, onClick }: {
  label: string;
  value: string;
  info: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="t-sm"
      style={{
        border: `1px solid ${active ? 'var(--fg-1, #111)' : 'var(--border, #d1d5db)'}`,
        borderRadius: 6,
        padding: '6px 10px',
        background: 'none',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
        {label}{' '}
        {/* 네이티브 title 툴팁 — 산출 정의(진기님 (i) 요구). 라이브러리 불요 */}
        <span title={info} style={{ cursor: 'help' }}>ⓘ</span>
      </span>
      <br />
      <b>{value}</b>
    </button>
  );
}

const STAGE_LABEL: Record<NewsLineageStage, string> = {
  structured: '구조화 증거 있음',
  unstructured: '구조화 증거 없음',
  used: '분석 사용',
};

export function NewsLineagePage() {
  /* 기본은 전체 누적 — 런 단위 계보는 불가하다(문서 테이블에 run_id 없음), 날짜로 자른다.
   * ?date= 는 다른 화면(Run Overview 뉴스 레인)이 특정 날짜로 내려보내는 손잡이다(ALPHA-692) —
   * 초기값만 받고 이후 변경은 로컬 상태(기존 동작 유지). */
  const [params] = useSearchParams();
  const [date, setDate] = useState<string>(params.get('date') ?? '');
  /* 표본 크기 — 서버 상한 200. 전량 페이지네이션은 후속(표본 검증 경로가 이 화면의 계약) */
  const [limit, setLimit] = useState<number>(50);
  /* 타일 클릭 드릴다운(ALPHA-697) — 필터는 목록만 좁히고 집계 타일 분모는 유지된다(서버 계약) */
  const [stage, setStage] = useState<NewsLineageStage | undefined>(undefined);
  const { data, isPending, isError, error } = useNewsLineage(date || undefined, limit, stage);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  const s = data.summary;
  const m = s.totalDocuments;
  const unstructured = m - s.documentsWithAssertion;
  const ex = data.extraction;
  const exTotal = ex.succeeded + ex.dead;
  const dateScope = data.date ? `수집일(KST)=${data.date}` : '전체 누적';
  const toggle = (next: NewsLineageStage) => setStage(stage === next ? undefined : next);

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">뉴스 계보</span>
          {/* 네이티브 date input — 라이브러리 불요 */}
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="t-xs"
            style={{ border: '1px solid var(--border, #d1d5db)', borderRadius: 4, padding: '2px 6px' }}
          />
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {data.date ? `수집일(KST) ${data.date}` : '전체 누적'} · 단위=문서(기사) · 타일 클릭=그 단계 목록
          </span>
        </div>

        {/* funnel — 비율은 항상 N/M 병기, 산출 정의는 (i) */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <StageTile
            label="수집"
            value={`${m.toLocaleString()}건`}
            info={`document(NEWS) 중 수집 시각(available_at)의 KST 날짜가 조건(${dateScope})인 문서 수.`}
            active={stage === undefined}
            onClick={() => setStage(undefined)}
          />
          <StageTile
            label="구조화 증거 있음"
            value={`${s.documentsWithAssertion.toLocaleString()}/${m.toLocaleString()} (${pct(s.documentsWithAssertion, m)})`}
            info={'분자=구조화 증거(document_assertion)가 1건 이상 남은 문서 · 분모=수집 문서. "추출 성공"이 아니라 증거가 남았다는 사실이다.'}
            active={stage === 'structured'}
            onClick={() => toggle('structured')}
          />
          <StageTile
            label="구조화 증거 없음"
            value={`${unstructured.toLocaleString()}/${m.toLocaleString()} (${pct(unstructured, m)})`}
            info={'분자=증거 0건 문서 · 분모=수집 문서. 이벤트 없는 정상 기사(NO_EVENT)·추출 실패·미실행이 구분 없이 섞인 한 통이다 — 문서별 추출 판정은 아직 원장에 없다(승격 후속).'}
            active={stage === 'unstructured'}
            onClick={() => toggle('unstructured')}
          />
          <StageTile
            label="분석 사용"
            value={`${s.documentsUsedInAnalysis.toLocaleString()}/${m.toLocaleString()} (${pct(s.documentsUsedInAnalysis, m)})`}
            info={'분자=assertion→event_evidence→explanation_run 체인이 존재하는 문서 · 분모=수집 문서.'}
            active={stage === 'used'}
            onClick={() => toggle('used')}
          />
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
          ⚠️ 원장이 답할 수 있는 범위: 존재 → 증거 → 분석 사용. 중복 제거·종목 연결 단계는 계측 밖이다.
        </p>
      </div>

      {/* 장중 1분 추출 — 문서 표와 다른 원장(news_extraction_job)임을 명시한다 */}
      <div className="card">
        <div className="card-head">
          <span className="t-label">장중 1분 추출 job</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            <span
              title={`news_extraction_job 기준 · 날짜 축=job 생성 시각(KST, ${dateScope}) — 위 문서 표(수집 시각 축)와 다른 원장이라 분모가 다를 수 있다. EOD 레인 실패는 작업 단위로 파이프라인 실행 이력(/sources) 소관.`}
              style={{ cursor: 'help' }}
            >ⓘ 산출 기준</span>
          </span>
        </div>
        {exTotal === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            귀결(SUCCEEDED/DEAD)된 추출 job 없음 — 1분 파이프라인 미가동과 진행 중(대기·재시도)이
            구분되지 않는 0 이다. 진행 상태는 장중 1분 수집(/minute)이 답한다.
          </p>
        ) : (
          <>
            <p className="t-sm m-0">
              성공 <b>{ex.succeeded.toLocaleString()}건</b>
              {' · '}DEAD <b style={{ color: ex.dead > 0 ? 'var(--down, #b91c1c)' : undefined }}>
                {ex.dead.toLocaleString()}건
              </b>
              {' · '}실패 비중 <b>{ex.dead.toLocaleString()}/{exTotal.toLocaleString()} ({pct(ex.dead, exTotal)})</b>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}> — 분모=귀결(성공+DEAD)된 job</span>
            </p>
            {ex.deadByErrorCode.length > 0 && (
              <p className="t-xs m-0" style={{ marginTop: 4 }}>
                DEAD 사유별:{' '}
                {ex.deadByErrorCode.map((c, i) => (
                  <span key={c.errorCode ?? '(null)'}>
                    {i > 0 && ' · '}
                    <code>{c.errorCode ?? '(사유 미기록)'}</code> {c.count.toLocaleString()}건
                  </span>
                ))}
              </p>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">문서 목록</span>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="t-xs"
            style={{ border: '1px solid var(--border, #d1d5db)', borderRadius: 4, padding: '2px 4px' }}
            title="표본 크기 (서버 상한 200)"
          >
            <option value={50}>50건</option>
            <option value={200}>200건</option>
          </select>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {stage ? `필터: ${STAGE_LABEL[stage]} · ` : ''}수집 시각 내림차순 · 최근{' '}
            {data.documents.length}건 표본 — 위 집계의 검증 경로
          </span>
        </div>
        {data.documents.length === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            {stage
              ? '이 단계에 해당하는 문서가 없습니다.'
              : data.date ? '이 날짜에 수집된 문서가 없습니다.' : '수집된 문서가 없습니다.'}
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
              <thead>
                <tr className="t-xs" style={{ color: 'var(--fg-3)', textAlign: 'left' }}>
                  <th style={{ padding: '0 10px 4px 0' }}>제목</th>
                  <th style={{ padding: '0 10px 4px 0' }}>언론사</th>
                  <th style={{ padding: '0 10px 4px 0' }}>벤더</th>
                  <th style={{ padding: '0 10px 4px 0' }}>게시(KST)</th>
                  <th style={{ padding: '0 10px 4px 0' }}>수집(KST)</th>
                  <th style={{ padding: '0 10px 4px 0', textAlign: 'right' }}>구조화 증거</th>
                  <th style={{ padding: '0 0 4px 0' }}>분석 사용</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <DocumentRow key={d.documentId} d={d} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
