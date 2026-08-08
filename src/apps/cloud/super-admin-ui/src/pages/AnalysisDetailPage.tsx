import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Delta, Icon, PageSkeleton, StatusBadge, formatDelta, toast } from 'ui-kit';
import {
  ANALYSIS_CONFIDENCE_LABEL,
  ANALYSIS_PUBLICATION_LABEL,
  ANALYSIS_PUBLICATION_TONE,
  ANALYSIS_STATUS_LABEL,
  ANALYSIS_STATUS_TONE,
  BAND_LABEL,
  BASIS_LABEL,
  EVIDENCE_TYPE_CHIP,
  METHOD_LABEL,
  UNIT_LABEL,
} from '../domains/analyses';
import type { StatTestDetail } from '../domains/analyses';
import { useAnalysis, useAnalysisActions } from '../domains/analyses/hooks';
import { LoadError } from './_shared/LoadError';

export function AnalysisDetailPage() {
  const { id } = useParams();
  const { analysis: a, isPending, isError, error } = useAnalysis(id);
  const { invalidateAnalysis } = useAnalysisActions();

  const [confirmingInvalidate, setConfirmingInvalidate] = useState(false);
  const [invalidateReason, setInvalidateReason] = useState('');

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={5} />;
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

  return (
    <div className="flex max-w-[1100px] flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="t-h1">{a.name}</span>
        <span className="mono" style={{ color: 'var(--fg-3)' }}>{a.code}</span>
        <span className="tag">{a.market}</span>
        <Delta direction={a.direction} pct={a.changePct} style={{ fontSize: 16 }} />
        <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>{ANALYSIS_STATUS_LABEL[a.status]}</StatusBadge>
        {a.publicationStatus && (
          <StatusBadge tone={ANALYSIS_PUBLICATION_TONE[a.publicationStatus]}>
            {ANALYSIS_PUBLICATION_LABEL[a.publicationStatus]}
          </StatusBadge>
        )}
      </div>

      <div className="grid items-start gap-4" style={{ gridTemplateColumns: '1fr 340px' }}>
        {/* 좌측 — 근거·분석 결과 */}
        <div className="flex min-w-0 flex-col gap-4">
          <div className="card">
            <div className="card-head">
              <span className="t-label">근거 데이터</span>
              {/* 표시 상한에 잘렸으면 총 건수와 표시 건수를 같이 말한다 — 건수만 줄여 쓰면
                  근거가 그것뿐인 것으로 읽힌다. UI 와 API 는 따로 배포돼 총 건수를 아직 안 주는
                  응답을 만날 수 있다 — 그땐 종전대로 표시 건수를 말한다(빈 "건" 방지) */}
              <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
                {a.evidenceTotal ?? a.evidence.length}건
                {(a.evidenceTotal ?? 0) > a.evidence.length
                  && ` (상위 ${a.evidence.length}건 표시)`}
              </span>
            </div>
            <div className="flex flex-col">
              {a.evidence.map((e, i) => {
                // 통계검정 행은 시각이 없다(명세 §3.4) — 메타 줄이 요약 4조각으로 대체되고,
                // 출처(series)·추가정보는 펼쳐야 보인다(§10.4)
                const row = (
                  <div className="flex items-start gap-3 px-4 py-3">
                    <span className="chip mt-px flex-none">{evidenceChip(e.type)}</span>
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="t-body" style={{ fontWeight: 500 }}>{e.title}</span>
                      <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
                        {e.detail ? statSummary(e.detail) : `${e.source} · ${e.time}`}
                      </span>
                    </div>
                  </div>
                );
                return (
                  <div key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                    {e.detail ? (
                      <details>
                        <summary className="cursor-pointer" style={{ listStyle: 'none' }}>
                          {row}
                        </summary>
                        <StatTestDetailBody d={e.detail} />
                      </details>
                    ) : row}
                  </div>
                );
              })}
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
            {/* 고객 노출 문장(ALPHA-878) — 블록이 있으면 고객에게 실제로 나간 산문을
                순서대로, 각 문장 아래 그 문장의 근거 참조를 붙인다. 내부 산출은 API 가
                애초에 싣지 않는다. 블록이 없으면(구 런·미완료) 종전 원문 폴백 */}
            {a.resultBlocks?.length ? (
              <div className="flex flex-col">
                {a.resultBlocks.map((b, i) => (
                  <div
                    key={i}
                    className="flex flex-col gap-1 px-4 py-3"
                    style={{ borderBottom: '1px solid var(--border-faint)' }}
                  >
                    <span className="t-label">{b.title}</span>
                    <p
                      className="t-body m-0 whitespace-pre-line"
                      style={{ color: 'var(--fg-2)', lineHeight: 1.6 }}
                    >
                      {b.text}
                    </p>
                    {b.evidenceRefs.length > 0 && (
                      <span className="t-xs num break-all" style={{ color: 'var(--fg-3)' }}>
                        근거 {b.evidenceRefs.join(' · ')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-4">
                <p
                  className="t-body m-0 whitespace-pre-line"
                  style={{ color: 'var(--fg-2)', lineHeight: 1.6 }}
                >
                  {a.result}
                </p>
              </div>
            )}
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

          <div className="card">
            <div className="card-head">
              <span className="t-label">관리 액션</span>
            </div>
            <div className="flex flex-col gap-2 p-4">
              {/* 무효화(ALPHA-440) — 게시본을 내리고 전 수신 테넌트에 INVALIDATION 이
               * 전파된다. 되돌리기 없음(설계). 게시본(PUBLISHED)에서만 활성 — 구
               * 정정/제외/복원 오버레이는 ALPHA-737 로 은퇴했다. */}
              {a.publicationStatus === 'PUBLISHED' ? (
                confirmingInvalidate ? (
                  <div
                    className="flex flex-col gap-2 rounded-[5px] p-2.5"
                    style={{ background: 'var(--down-tint)', border: '1px solid var(--border)' }}
                  >
                    <span className="t-xs" style={{ color: 'var(--down)', fontWeight: 600 }}>
                      이 분석을 무효화하시겠습니까? 게시가 내려가고 전 테넌트 화면에서
                      제거됩니다. 되돌릴 수 없습니다.
                    </span>
                    <label className="field w-full box-border">
                      <input
                        placeholder="무효화 사유 (필수) — 감사 기록·테넌트 전파에 보존됩니다"
                        value={invalidateReason}
                        onChange={(e) => setInvalidateReason(e.target.value)}
                      />
                    </label>
                    <div className="flex gap-1.5">
                      <button
                        className="btn btn-sm flex-1 justify-center"
                        onClick={() => {
                          setConfirmingInvalidate(false);
                          setInvalidateReason('');
                        }}
                      >
                        취소
                      </button>
                      <button
                        className="btn btn-sm flex-1 justify-center"
                        style={{ color: '#fff', background: 'var(--down)', borderColor: 'var(--down)' }}
                        disabled={!invalidateReason.trim() || invalidateAnalysis.isPending}
                        onClick={() =>
                          invalidateAnalysis.mutate(
                            { id: a.id, reason: invalidateReason },
                            {
                              onSuccess: () => {
                                setConfirmingInvalidate(false);
                                setInvalidateReason('');
                                toast('분석이 무효화되었습니다 — 테넌트 화면에서 제거됩니다.');
                              },
                            },
                          )
                        }
                      >
                        무효화
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    className="btn justify-center"
                    style={{ color: 'var(--down)' }}
                    onClick={() => setConfirmingInvalidate(true)}
                  >
                    <Icon name="ban" className="ic" />
                    분석 무효화
                  </button>
                )
              ) : (
                <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                  {a.publicationStatus === 'WITHDRAWN'
                    ? '이미 무효화된 분석입니다.'
                    : '게시된 분석만 무효화할 수 있습니다.'}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 코드→라벨 — 미지 코드(스키마가 라벨보다 먼저 진화한 경우)는 원문 코드 폴백. crash 금지(신뢰 경계). */
function codeLabel<K extends string>(labels: Record<K, string>, code: string) {
  return labels[code as K] ?? code;
}

/** chip 축약 라벨 — 코드만 히트, 구 API 의 한글·미지 코드는 원문 폴백(코드↔라벨 계약 §10.3). */
function evidenceChip(type: string) {
  return codeLabel(EVIDENCE_TYPE_CHIP, type);
}

/** 평균 차이 — 비율 저장값을 ×100 해 부호 붙은 %p 로(명세 §4). */
function signedPctPoint(ratio: number) {
  return `${ratio >= 0 ? '+' : ''}${(ratio * 100).toFixed(2)}%p`;
}

/** p 표기 — %.4f(명세 §4). 양수 p 가 반올림으로 0.0000 이 되면 거짓이라 p<0.0001 로 만다. */
function pLabel(p: number) {
  return p > 0 && p < 0.00005 ? 'p<0.0001' : `p=${p.toFixed(4)}`;
}

/**
 * 통계검정 추가정보를 [라벨, 값] 조각으로 — 결측 조각은 구분자까지 함께 생략한다(명세 §4).
 * API 가 채우다 만 detail(런타임 null·미지 enum)에도 행이 깨지지 않고 정직하게 줄어든다.
 */
function statPieces(d: StatTestDetail): [string, string][] {
  const rows: [string, string][] = [];
  if (d.method) rows.push(['방법', codeLabel(METHOD_LABEL, d.method)]);
  if (Number.isFinite(d.n)) {
    rows.push(['표본', `과거 ${d.n}${d.unit ? codeLabel(UNIT_LABEL, d.unit) : ''}`]);
  }
  if (Number.isFinite(d.estimate)) rows.push(['차이', `평균 ${signedPctPoint(d.estimate)}`]);
  if (Number.isFinite(d.p)) {
    // 가설 보정 조각은 k>1 일 때만 — k=1 이면 보정이 없어 조각을 생략한다(명세 §3.4)
    rows.push(['유의확률', `${pLabel(d.p)}${(d.k ?? 1) > 1 ? ` · 가설 ${d.k}건 보정` : ''}`]);
  }
  return rows;
}

/** 접힌 줄 요약 — 방법·표본·차이·유의확률 넷만(기준·위치까지 넣으면 리스트가 무너진다, §10.4). */
function statSummary(d: StatTestDetail) {
  return statPieces(d)
    .map(([, value]) => value)
    .join(' · ');
}

/** 통계검정 펼침 영역 — 출처(series) 한 줄 + 추가정보(명세 §3.4). C6 배선 전까지 데이터는 오지 않는다. */
function StatTestDetailBody({ d }: { d: StatTestDetail }) {
  const rows: [string, string][] = [
    // 기준이 맨 위(명세 §3.4 순서) — 값이 없으면 줄 생략
    ...(d.basis ? ([['기준', codeLabel(BASIS_LABEL, d.basis)]] as [string, string][]) : []),
    ...statPieces(d),
  ];
  // 위치는 값이 없으면 줄 자체를 생략한다(명세 §3.6). 미지 band 코드는 원문 폴백
  if (d.band) rows.push(['위치', codeLabel(BAND_LABEL, d.band)]);
  return (
    <div className="flex flex-col gap-1 px-4 pb-3" style={{ paddingLeft: 52 }}>
      {(d.series?.length ?? 0) > 0 && (
        <span className="t-xs num break-all" style={{ color: 'var(--fg-3)' }}>
          {d.series.join(' · ')}
        </span>
      )}
      {rows.map(([label, value]) => (
        <span key={label} className="t-xs" style={{ color: 'var(--fg-2)' }}>
          <span style={{ color: 'var(--fg-3)', display: 'inline-block', minWidth: 56 }}>
            {label}
          </span>
          <span className="num">{value}</span>
        </span>
      ))}
    </div>
  );
}
