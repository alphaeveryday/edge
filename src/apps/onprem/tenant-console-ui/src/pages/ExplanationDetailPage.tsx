import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Icon, PageSkeleton, StatusBadge, toast } from 'ui-kit';
import {
  CONFIDENCE_LABEL, CONFIDENCE_TONE, PUBLISHED_STATUSES, STATUS_LABEL, STATUS_TONE,
} from '../domains/explanations';
import { useExplanation, useExplanationActions } from '../domains/explanations/hooks';
import { useSession } from '../domains/session/hooks';
import { isHttpUrl } from './_shared/links';
import { LoadError } from './_shared/cells';

export function ExplanationDetailPage() {
  const { id } = useParams();
  const { explanation: it, isLoading, isError } = useExplanation(id);
  const { updateFinal, stop, moveToReview } = useExplanationActions();
  const { data: session } = useSession();
  // 강제 지점은 API(ConsoleAuthFilter), 화면은 UX 게이트(permission-matrix "이중 방어").
  // 최종 문구 수정 = CR, 노출 축소(이관·중단) = CR·OP.
  const canEditFinal = session?.role === 'COMPLIANCE_REVIEWER';
  const canReduceExposure =
    session?.role === 'COMPLIANCE_REVIEWER' || session?.role === 'OPERATOR';

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [stopping, setStopping] = useState(false);
  const [stopReason, setStopReason] = useState('');

  if (isError) return <LoadError />;
  if (isLoading) return <PageSkeleton rows={5} />;
  if (!it) {
    return (
      <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
        해당 가격 변동 설명을 찾을 수 없습니다.
      </div>
    );
  }

  return (
    <div className="flex max-w-[860px] flex-col gap-4">
      <div>
        <Link to="/explanations" className="inline-flex items-center gap-1" style={{ fontSize: 12 }}>
          <Icon name="chevronLeft" size={13} strokeWidth={1.8} />
          가격 변동 설명 목록
        </Link>
      </div>

      <div className="card card-pad flex flex-wrap items-center gap-6">
        <div className="min-w-[180px]">
          <div style={{ fontSize: 18, fontWeight: 700 }}>
            {it.name}{' '}
            <span className="num" style={{ fontSize: 13, fontWeight: 400, color: 'var(--fg-4)' }}>
              {it.code}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--fg-3)', marginTop: 2 }}>
            {it.receivedAt}
          </div>
        </div>
        <div className="flex items-center gap-8">
          <div>
            <div className="t-label">제공 상태</div>
            <div className="mt-1.5 flex items-center gap-1.5">
              <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status]}</StatusBadge>
              {/* 노출 head(ALPHA-744) — 같은 종목 다스냅샷 중 지금 고객 화면에 보이는 판 */}
              {it.serving && (
                <StatusBadge tone="exposed" dot={false}>
                  노출 중
                </StatusBadge>
              )}
            </div>
          </div>
          <div>
            <div className="t-label">기준시각</div>
            <div className="num mt-1.5" style={{ fontSize: 12 }}>
              {it.explanationAsOf}
            </div>
          </div>
          <div>
            <div className="t-label">확신도</div>
            <div className="mt-1.5">
              {it.confidence ? (
                <StatusBadge tone={CONFIDENCE_TONE[it.confidence]} dot={false}>
                  {CONFIDENCE_LABEL[it.confidence]}
                </StatusBadge>
              ) : (
                <span style={{ color: 'var(--fg-4)' }}>—</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex-1" />
        <div className="flex gap-2">
          {/* 최종 문구는 게시본(published_summary)을 정정하므로 노출 중일 때만 — 미게시 건은 API 가 409 */}
          {canEditFinal && PUBLISHED_STATUSES.includes(it.status) && (
            <button
              className="btn"
              onClick={() => {
                setEditing(true);
                setDraft(it.final);
              }}
            >
              최종 문구 수정
            </button>
          )}
          {canReduceExposure && it.status === 'BLOCKED' && (
            <button
              className="btn"
              onClick={() =>
                moveToReview.mutate(it.id, {
                  onSuccess: () => toast(`${it.name} 설명이 검수 대기열로 이관되었습니다.`),
                })
              }
            >
              검수로 이관
            </button>
          )}
          {/* 실제 노출 중인 상태에서만 — 검수 대기·차단·반려 건은 승인/반려 플로우를 우회하지 않게 */}
          {canReduceExposure && PUBLISHED_STATUSES.includes(it.status) && (
            <button className="btn btn-danger" onClick={() => setStopping(true)}>
              제공 중단
            </button>
          )}
        </div>
      </div>

      {/* 제공 중단 사유 — 필수(감사·publication unpublish_reason). ReviewDetailPage 반려/차단 사유 패턴 이식 */}
      {stopping && (
        <div className="card card-pad flex flex-col gap-2">
          <span className="t-label">제공 중단 사유</span>
          <textarea
            className="textarea"
            rows={3}
            placeholder="중단 사유를 입력하세요 — 감사 기록에 남습니다."
            value={stopReason}
            onChange={(e) => setStopReason(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button
              className="btn btn-sm"
              onClick={() => {
                setStopping(false);
                setStopReason('');
              }}
            >
              취소
            </button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => {
                const reason = stopReason.trim();
                if (!reason) {
                  toast('제공 중단 사유를 입력하세요.');
                  return;
                }
                stop.mutate(
                  { id: it.id, reason },
                  {
                    onSuccess: () => {
                      setStopping(false);
                      setStopReason('');
                      toast(`${it.name} 설명 제공이 중단되었습니다.`);
                    },
                  },
                );
              }}
            >
              제공 중단
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <span className="t-label">근거 데이터</span>
          <span className="num" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            {it.evidence.length}개 출처
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>유형</th>
              <th>내용</th>
              <th>출처</th>
              <th className="col-muted">시각</th>
            </tr>
          </thead>
          <tbody>
            {it.evidence.map((ev, i) => (
              <tr key={i}>
                <td>
                  <span className="chip">{ev.type}</span>
                </td>
                <td>
                  {/* 원문 링크(ALPHA-739) — 결측(EOD 구멍 등)·비웹 URI 는 일반 텍스트 폴백 */}
                  {ev.sourceUri && isHttpUrl(ev.sourceUri) ? (
                    <a href={ev.sourceUri} target="_blank" rel="noopener noreferrer">
                      {ev.title}
                    </a>
                  ) : (
                    ev.title
                  )}
                </td>
                <td className="col-muted">{ev.source}</td>
                <td className="col-muted num">{ev.time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="card">
          <div className="card-head">
            <span className="t-label">원본 설명 문구</span>
            <span className="chip">모델 생성</span>
          </div>
          <div className="p-4" style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--fg-2)' }}>
            {it.original}
          </div>
        </div>
        <div className="card" style={{ borderColor: editing ? 'var(--accent)' : 'var(--border)' }}>
          <div className="card-head">
            <span className="t-label">최종 제공 문구</span>
            <span className="chip chip-accent">제공본</span>
          </div>
          {editing ? (
            <div className="flex flex-col gap-2 p-4">
              <textarea
                className="textarea"
                style={{ borderColor: 'var(--accent)' }}
                rows={5}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <div className="flex justify-end gap-2">
                <button className="btn btn-sm" onClick={() => setEditing(false)}>
                  취소
                </button>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={() =>
                    updateFinal.mutate(
                      { id: it.id, final: draft },
                      {
                        onSuccess: () => {
                          setEditing(false);
                          toast('최종 제공 문구가 저장되었습니다.');
                        },
                      },
                    )
                  }
                >
                  저장
                </button>
              </div>
            </div>
          ) : (
            <div className="p-4" style={{ fontSize: 13, lineHeight: 1.65 }}>
              {it.final}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
