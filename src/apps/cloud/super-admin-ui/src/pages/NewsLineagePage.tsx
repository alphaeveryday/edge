/* 뉴스 계보 — Dataset Explorer 첫 슬라이스 (ALPHA-685).
 *
 * 존재 이유: "표시된 집계값을 목록으로 검증할 길"(멘토: "4천 건은 어디 있어"). 그래서 이
 * 화면의 모든 숫자는 아래 문서 표가 근거다 — 목록 없는 집계를 만들지 않는다.
 *
 * 주장의 한계(정직 표기): 원장(RDS)이 아는 건 문서의 존재 → 구조화 증거(assertion) →
 * 분석 사용까지다. 중복 제거·종목 연결·추출 terminal(NO_EVENT/실패 구분)은 S3 로그에만
 * 있어 여기서 주장하지 않는다 — "증거 없음"은 그 전부를 합친 한 통이다(승격은 후속 티켓).
 */
import { useState } from 'react';
import { PageSkeleton } from 'ui-kit';
import type { NewsLineageDocument } from '../domains/sources';
import { useNewsLineage } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

const fmt = (iso: string | null) =>
  iso ? `${new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}` : '—';

function DocumentRow({ d }: { d: NewsLineageDocument }) {
  return (
    <tr>
      <td style={{ padding: '3px 10px 3px 0', maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.title ?? ''}>
        {d.title ?? '—'}
      </td>
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

export function NewsLineagePage() {
  /* 기본은 전체 누적 — 런 단위 계보는 불가하다(문서 테이블에 run_id 없음), 날짜로 자른다 */
  const [date, setDate] = useState<string>('');
  const { data, isPending, isError, error } = useNewsLineage(date || undefined);

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  const s = data.summary;

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
            {data.date ? `수집일(KST) ${data.date}` : '전체 누적'} · 단위=문서(기사)
          </span>
        </div>

        {/* 계보 요약 — 각 숫자의 근거는 아래 문서 표다 */}
        <p className="t-sm m-0">
          수집 문서 <b>{s.totalDocuments.toLocaleString()}건</b>
          {' → '}구조화 증거 남음 <b>{s.documentsWithAssertion.toLocaleString()}건</b>
          {' → '}분석에 사용 <b>{s.documentsUsedInAnalysis.toLocaleString()}건</b>
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
          ⚠️ 원장이 답할 수 있는 범위: 존재 → 증거 → 분석 사용. "증거 없음"에는 이벤트 없는
          정상 기사(NO_EVENT)·추출 실패·미실행이 <b>구분 없이</b> 섞여 있다 — 문서별 추출
          판정은 아직 원장에 없다(후속). 중복 제거·종목 연결 단계도 계측 밖이다.
        </p>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">문서 목록</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            수집 시각 내림차순 · 최근 {data.documents.length}건 표본 — 위 집계의 검증 경로
          </span>
        </div>
        {data.documents.length === 0 ? (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            {data.date ? '이 날짜에 수집된 문서가 없습니다.' : '수집된 문서가 없습니다.'}
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
              <thead>
                <tr className="t-xs" style={{ color: 'var(--fg-3)', textAlign: 'left' }}>
                  <th style={{ padding: '0 10px 4px 0' }}>제목</th>
                  <th style={{ padding: '0 10px 4px 0' }}>출처</th>
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
