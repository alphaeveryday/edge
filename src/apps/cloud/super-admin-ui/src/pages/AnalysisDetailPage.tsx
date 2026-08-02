import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Delta, Icon, PageSkeleton, StatusBadge, formatDelta, toast } from 'ui-kit';
import {
  ANALYSIS_CONFIDENCE_LABEL,
  ANALYSIS_STATUS_LABEL,
  ANALYSIS_STATUS_TONE,
} from '../domains/analyses';
import { useAnalysis, useAnalysisActions } from '../domains/analyses/hooks';
import { LoadError } from './_shared/LoadError';

export function AnalysisDetailPage() {
  const { id } = useParams();
  const { analysis: a, isPending, isError, error } = useAnalysis(id);
  const { correct, exclude, restore } = useAnalysisActions();

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [correctReason, setCorrectReason] = useState('');
  const [confirmingExclude, setConfirmingExclude] = useState(false);
  const [excludeReason, setExcludeReason] = useState('');

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
        {a.corrected && <span className="chip chip-accent">정정됨</span>}
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
            {editing ? (
              <div className="flex flex-col gap-2.5 p-4">
                <textarea
                  className="textarea"
                  style={{ borderColor: 'var(--accent)', lineHeight: 1.6 }}
                  rows={6}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <div className="flex flex-col gap-1">
                  <span className="t-label">
                    정정 사유 <span style={{ color: 'var(--down)' }}>*</span>
                  </span>
                  <label className="field w-full box-border">
                    <input
                      placeholder="정정 사유 — 감사 기록에 보존됩니다"
                      value={correctReason}
                      onChange={(e) => setCorrectReason(e.target.value)}
                    />
                  </label>
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    className="btn"
                    onClick={() => {
                      setEditing(false);
                      setCorrectReason('');
                    }}
                  >
                    취소
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={!draft.trim() || !correctReason.trim() || correct.isPending}
                    onClick={() =>
                      correct.mutate(
                        { id: a.id, result: draft, reason: correctReason },
                        {
                          onSuccess: () => {
                            setEditing(false);
                            setCorrectReason('');
                            toast('분석 결과가 정정되었습니다.');
                          },
                        },
                      )
                    }
                  >
                    정정 내용 저장
                  </button>
                </div>
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
              <button
                className="btn justify-center"
                onClick={() => {
                  setEditing(true);
                  setDraft(a.result);
                }}
              >
                <Icon name="pencil" className="ic" />
                분석 결과 정정
              </button>
              {a.status !== 'EXCLUDED' &&
                (confirmingExclude ? (
                  <div
                    className="flex flex-col gap-2 rounded-[5px] p-2.5"
                    style={{ background: 'var(--down-tint)', border: '1px solid var(--border)' }}
                  >
                    <span className="t-xs" style={{ color: 'var(--down)', fontWeight: 600 }}>
                      이 종목을 분석 대상에서 제외하시겠습니까? 제외 후에는 테넌트에 분석 결과가 노출되지 않습니다.
                    </span>
                    <label className="field w-full box-border">
                      <input
                        placeholder="제외 사유 (필수) — 감사 기록에 보존됩니다"
                        value={excludeReason}
                        onChange={(e) => setExcludeReason(e.target.value)}
                      />
                    </label>
                    <div className="flex gap-1.5">
                      <button
                        className="btn btn-sm flex-1 justify-center"
                        onClick={() => {
                          setConfirmingExclude(false);
                          setExcludeReason('');
                        }}
                      >
                        취소
                      </button>
                      <button
                        className="btn btn-sm flex-1 justify-center"
                        style={{ color: '#fff', background: 'var(--down)', borderColor: 'var(--down)' }}
                        disabled={!excludeReason.trim() || exclude.isPending}
                        onClick={() =>
                          exclude.mutate(
                            { id: a.id, reason: excludeReason },
                            {
                              onSuccess: () => {
                                setConfirmingExclude(false);
                                setExcludeReason('');
                                toast('분석 대상에서 제외되었습니다.');
                              },
                            },
                          )
                        }
                      >
                        제외
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    className="btn justify-center"
                    style={{ color: 'var(--down)' }}
                    onClick={() => setConfirmingExclude(true)}
                  >
                    <Icon name="ban" className="ic" />
                    분석 대상 제외
                  </button>
                ))}
              {a.status === 'EXCLUDED' && (
                <button
                  className="btn justify-center"
                  disabled={restore.isPending}
                  onClick={() =>
                    restore.mutate(a.id, { onSuccess: () => toast('분석 대상으로 복원되었습니다.') })
                  }
                >
                  분석 대상 복원
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
