import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Delta, Icon, StatusBadge, toast } from 'ui-kit';
import {
  MARKET_DESC, PUBLISHED_STATUSES, RISK_LABEL, RISK_TONE, STATUS_LABEL, STATUS_TONE,
} from '../domains/explanations';
import { useExplanation, useExplanationActions } from '../domains/explanations/hooks';
import { LoadError } from './_shared/cells';

export function ExplanationDetailPage() {
  const { id } = useParams();
  const { explanation: it, isLoading, isError } = useExplanation(Number(id));
  const { updateFinal, stop, moveToReview } = useExplanationActions();

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  if (isError) return <LoadError />;
  if (isLoading) return null;
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
            {MARKET_DESC[it.market]} · {it.receivedAt}
          </div>
        </div>
        <div className="flex items-center gap-8">
          <div>
            <div className="t-label">등락률</div>
            <Delta direction={it.direction} pct={it.changePct} style={{ fontSize: 18, marginTop: 4, display: 'inline-block' }} />
          </div>
          <div>
            <div className="t-label">제공 상태</div>
            <div className="mt-1.5">
              <StatusBadge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status]}</StatusBadge>
            </div>
          </div>
          <div>
            <div className="t-label">위험 등급</div>
            <div className="mt-1.5">
              <StatusBadge tone={RISK_TONE[it.risk]} dot={false}>
                {RISK_LABEL[it.risk]}
              </StatusBadge>
            </div>
          </div>
        </div>
        <div className="flex-1" />
        <div className="flex gap-2">
          <button
            className="btn"
            onClick={() => {
              setEditing(true);
              setDraft(it.final);
            }}
          >
            최종 문구 수정
          </button>
          {it.status === 'BLOCKED' && (
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
          {PUBLISHED_STATUSES.includes(it.status) && (
            <button
              className="btn btn-danger"
              onClick={() =>
                stop.mutate(it.id, {
                  onSuccess: () => toast(`${it.name} 설명 제공이 중단되었습니다.`),
                })
              }
            >
              제공 중단
            </button>
          )}
        </div>
      </div>

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
                <td>{ev.title}</td>
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
