import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Delta, Icon, StatusBadge, toast } from 'ui-kit';
import {
  MARKET_DESC, REASON_DESC, REASON_LABEL, RISK_LABEL, RISK_TONE, STATUS_LABEL,
} from '../domains/explanations';
import { useExplanation, useExplanationActions } from '../domains/explanations/hooks';
import { LoadError } from './_shared/cells';

export function ReviewDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { explanation: it, isLoading, isError } = useExplanation(Number(id));
  const { approve, reject, saveDraft } = useExplanationActions();

  // 로드 전에는 undefined — 화면에는 최종 문구를 초기값으로 보여준다
  const [draft, setDraft] = useState<string>();
  const [note, setNote] = useState('');

  if (isError) return <LoadError />;
  if (isLoading) return null;
  if (!it) {
    return (
      <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
        해당 검수 항목을 찾을 수 없습니다.
      </div>
    );
  }
  // 검수 대기 상태가 아닌 항목은 승인·반려 표면 자체를 열지 않는다 (URL 직접 진입 가드)
  if (it.status !== 'REVIEW_REQUIRED') {
    return (
      <div className="flex max-w-[860px] flex-col gap-4">
        <div>
          <Link to="/review" className="inline-flex items-center gap-1" style={{ fontSize: 12 }}>
            <Icon name="chevronLeft" size={13} strokeWidth={1.8} />
            검수 대기 목록
          </Link>
        </div>
        <div className="card card-pad" style={{ fontSize: 12, color: 'var(--fg-2)' }}>
          {it.name}({it.code}) 설명은 현재 검수 대기 상태가 아닙니다 — {STATUS_LABEL[it.status]}.
          상세는 <Link to={`/explanations/${it.id}`}>가격 변동 설명 상세</Link>에서 확인하세요.
        </div>
      </div>
    );
  }

  const finalDraft = draft ?? it.final;

  return (
    <div className="flex max-w-[860px] flex-col gap-4">
      <div>
        <Link to="/review" className="inline-flex items-center gap-1" style={{ fontSize: 12 }}>
          <Icon name="chevronLeft" size={13} strokeWidth={1.8} />
          검수 대기 목록
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
        <div>
          <div className="t-label">등락률</div>
          <Delta direction={it.direction} pct={it.changePct} style={{ fontSize: 18, marginTop: 4, display: 'inline-block' }} />
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

      {it.reviewReason && (
        <div
          className="card card-pad flex items-start gap-3"
          style={{ background: 'var(--warn-tint)', borderColor: 'rgba(154,106,23,.25)' }}
        >
          <Icon name="alertTriangle" size={16} strokeWidth={1.8} className="mt-px flex-none text-(--warn)" />
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--warn)' }}>
              검수 사유 — {REASON_LABEL[it.reviewReason]}
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-2)', marginTop: 2 }}>
              {REASON_DESC[it.reviewReason]}
            </div>
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
                <td>{ev.title}</td>
                <td className="col-muted">{ev.source}</td>
                <td className="col-muted num">{ev.time}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">원본 설명 문구</span>
          <span className="chip">모델 생성</span>
        </div>
        <div className="p-4" style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--fg-2)' }}>
          {it.original}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">최종 문구 (수정 가능)</span>
        </div>
        <div className="p-4">
          <textarea className="textarea" rows={4} value={finalDraft} onChange={(e) => setDraft(e.target.value)} />
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">검수 의견</span>
        </div>
        <div className="p-4">
          <textarea
            className="textarea"
            rows={2}
            placeholder="검수 판단 근거를 기록합니다. 감사 로그에 저장됩니다."
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <button
          className="btn"
          onClick={() =>
            saveDraft.mutate(
              { id: it.id, final: finalDraft },
              { onSuccess: () => toast('임시 저장되었습니다.') },
            )
          }
        >
          임시 저장
        </button>
        <button
          className="btn btn-danger"
          onClick={() =>
            reject.mutate(
              { id: it.id, note },
              {
                onSuccess: () => {
                  toast(`${it.name} 설명이 반려되었습니다.`);
                  navigate('/review');
                },
              },
            )
          }
        >
          반려
        </button>
        <button
          className="btn btn-primary"
          onClick={() =>
            approve.mutate(
              { id: it.id, final: finalDraft, note },
              {
                onSuccess: () => {
                  toast(`${it.name} 설명이 승인되어 제공됩니다.`);
                  navigate('/review');
                },
              },
            )
          }
        >
          승인 후 제공
        </button>
      </div>
    </div>
  );
}
