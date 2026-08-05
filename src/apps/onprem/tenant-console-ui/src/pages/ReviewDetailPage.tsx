import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Icon, PageSkeleton, StatusBadge, toast } from 'ui-kit';
import {
  CHECK_RESULT_LABEL,
  EVIDENCE_KIND_LABEL,
  ITEM_STATUS_LABEL,
  matchedLabel,
  reasonLabel,
} from '../domains/review';
import { apiMessage } from '../api/client';
import { useReviewActions, useReviewItem } from '../domains/review/hooks';
import { isHttpUrl } from './_shared/links';
import { useSession } from '../domains/session/hooks';
import { LoadError } from './_shared/cells';

/**
 * 검수 상세(ALPHA-436, 구 439 흡수) — 실 analysis_item 원장 기반: 원문·근거(evidences)·
 * 파생 사유(screening_check)·검사 결과·상태 변경 이력. 액션은 실계약 4종(승인·수정
 * 승인·반려·차단 — 임시 저장은 실계약에 없어 편집은 화면 로컬, 승인 시 수정 승인으로
 * 전달). 감사·노출 이력은 별도 메뉴가 아니라 이 상세로 확인한다(콘솔 IA).
 */
export function ReviewDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: it, isError, isPending } = useReviewItem(id);
  const actions = useReviewActions(id ?? '');
  const { data: session } = useSession();
  // 검수 액션은 CR 전용(permission-matrix) — 강제 지점은 API 필터, 화면은 UX 게이트.
  const canReview = session?.role === 'COMPLIANCE_REVIEWER';

  const [finalText, setFinalText] = useState<string>();
  const [note, setNote] = useState('');

  if (isError) return <LoadError />;
  if (isPending) return <PageSkeleton rows={5} />;
  if (!it) return null;

  const edited = finalText !== undefined && finalText.trim() !== it.summary;
  const inReview = it.status === 'REVIEW_REQUIRED';
  const reviewChecks = it.checks.filter((c) => c.result === 'REVIEW');

  const done = (msg: string) => () => {
    toast(msg);
    navigate('/review');
  };
  // 서버 사유(상태 경쟁 409 등) 우선, 없으면 복구 안내 폴백 — 전역(일반 문구)보다 늦게 떠 덮는다.
  const fail = (err: unknown) => {
    toast(apiMessage(err, '처리하지 못했습니다 — 목록을 새로고침해 주세요.'));
  };

  const approve = () => {
    const text = (finalText ?? it.summary).trim();
    if (!text) {
      toast('최종 문구가 비어 있습니다.');
      return;
    }
    if (edited) {
      actions.approveEdited.mutate(
        { editedSummary: text, note: note.trim() || null },
        { onSuccess: done('수정 승인되어 제공됩니다.'), onError: fail },
      );
    } else {
      actions.approve.mutate(note.trim() || null, {
        onSuccess: done('승인되어 제공됩니다.'),
        onError: fail,
      });
    }
  };

  const rejectOrBlock = (kind: 'reject' | 'block') => {
    const reason = note.trim();
    if (!reason) {
      toast(kind === 'reject' ? '반려 사유를 검수 의견에 입력하세요.' : '차단 사유를 검수 의견에 입력하세요.');
      return;
    }
    actions[kind].mutate(reason, {
      onSuccess: done(kind === 'reject' ? '반려 처리했습니다.' : '차단 처리했습니다.'),
      onError: fail,
    });
  };

  return (
    <div className="flex max-w-[900px] flex-col gap-4">
      <div className="flex items-center gap-2">
        <Link to="/review" className="btn btn-sm btn-ghost">
          <Icon name="arrowLeft" className="ic" /> 목록
        </Link>
        <span className="font-semibold" style={{ fontSize: 15 }}>
          {it.name ?? '(종목 미상)'}
        </span>
        <span className="col-muted num" style={{ fontSize: 12 }}>
          {it.ticker ?? '—'} · {it.tradeDate ?? '—'}
        </span>
        <StatusBadge tone={inReview ? 'warn' : 'neutral'}>
          {ITEM_STATUS_LABEL[it.status] ?? it.status}
        </StatusBadge>
        <div className="flex-1" />
        <span className="col-muted num" style={{ fontSize: 11 }}>
          수신 {it.receivedAt ? new Date(it.receivedAt).toLocaleString('sv-SE').slice(0, 16) : '—'}
        </span>
      </div>

      {(it.reviewReasons.length > 0 || reviewChecks.length > 0) && (
        <div
          className="rounded-[5px] p-3"
          style={{ border: '1px solid var(--warn)', background: 'var(--bg-sunken)' }}
        >
          <div className="t-label mb-1.5" style={{ color: 'var(--warn)' }}>
            검수 사유
          </div>
          <div className="flex flex-wrap items-center gap-1.5" style={{ fontSize: 12 }}>
            {it.reviewReasons.map((r) => (
              <StatusBadge key={r} tone="warn">
                {reasonLabel(r)}
              </StatusBadge>
            ))}
            {/* 근거는 판정기가 남긴 원값이라 게이트 판정은 기계 문자열이다(source_events=2 등)
                — 화면에서 해석한다(ALPHA-770). 해석 못 한 형식은 원문 그대로 나온다. */}
            {reviewChecks
              .filter((c) => matchedLabel(c.matchedText, c.ruleType) !== null)
              .map((c, i) => (
                <span key={i} className="col-muted" title={c.matchedText ?? undefined}>
                  {matchedLabel(c.matchedText, c.ruleType)}
                </span>
              ))}
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <span className="t-label">원본 설명 문구</span>
        </div>
        <div className="p-4" style={{ fontSize: 13, lineHeight: 1.7 }}>
          {it.summary}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">근거 데이터</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>유형</th>
              <th>내용</th>
              <th className="col-muted">출처</th>
              <th className="col-muted">시각</th>
            </tr>
          </thead>
          <tbody>
            {it.evidences.map((e, i) => (
              <tr key={i}>
                <td>{EVIDENCE_KIND_LABEL[e.kind] ?? e.kind}</td>
                <td>
                  {/* 원문 링크(ALPHA-739) — 결측(EOD 구멍 등)·비웹 URI 는 일반 텍스트 폴백 */}
                  {e.sourceUri && isHttpUrl(e.sourceUri) ? (
                    <a href={e.sourceUri} target="_blank" rel="noopener noreferrer">
                      {e.title}
                    </a>
                  ) : (
                    e.title
                  )}
                </td>
                <td className="col-muted">{e.source}</td>
                <td className="col-muted t-data">
                  {e.publishedAt ? new Date(e.publishedAt).toLocaleString('sv-SE').slice(0, 16) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {it.evidences.length === 0 && (
          <div className="p-6 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            근거 데이터가 없습니다.
          </div>
        )}
      </div>

      {canReview && inReview && (
        <div className="card">
          <div className="card-head">
            <span className="t-label">최종 노출 문구 (수정 시 “수정 승인”으로 처리)</span>
          </div>
          <div className="flex flex-col gap-3 p-4">
            <textarea
              className="textarea"
              rows={4}
              value={finalText ?? it.summary}
              onChange={(e) => setFinalText(e.target.value)}
            />
            <div>
              <div className="t-label mb-1.5">검수 의견 (반려·차단 시 사유로 기록)</div>
              <textarea
                className="textarea"
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="예: 단정 표현 제거"
              />
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={() => rejectOrBlock('block')}>
                차단
              </button>
              <button className="btn" onClick={() => rejectOrBlock('reject')}>
                반려
              </button>
              <button className="btn btn-primary" onClick={approve}>
                {edited ? '수정 승인 후 제공' : '승인 후 제공'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <span className="t-label">컴플라이언스 검사 결과</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>판정</th>
              <th>룰 유형</th>
              <th>매칭 근거</th>
              <th className="col-muted">검사 시각</th>
            </tr>
          </thead>
          <tbody>
            {it.checks.map((c, i) => (
              <tr key={i}>
                <td>
                  <StatusBadge tone={c.result === 'PASS' ? 'active' : c.result === 'REVIEW' ? 'warn' : 'blocked'}>
                    {CHECK_RESULT_LABEL[c.result] ?? c.result}
                  </StatusBadge>
                </td>
                <td>{c.ruleType ? reasonLabel(c.ruleType) : '기준 판정'}</td>
                {/* 해석문을 보여주되 원값은 title 로 남긴다 — 감사·장애 문의에서 판정기가
                    남긴 문자열 그대로가 필요하다(원장 값과 대조). */}
                <td className="col-muted" title={c.matchedText ?? undefined}>
                  {matchedLabel(c.matchedText, c.ruleType) ?? '—'}
                </td>
                <td className="col-muted t-data">
                  {c.checkedAt ? new Date(c.checkedAt).toLocaleString('sv-SE').slice(0, 19) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {it.checks.length === 0 && (
          <div className="p-6 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            기록된 검사 결과가 없습니다.
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">상태 변경 이력</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>전이</th>
              <th>행위자</th>
              <th>사유</th>
              <th className="col-muted">시각</th>
            </tr>
          </thead>
          <tbody>
            {it.history.map((h, i) => (
              <tr key={i}>
                <td>
                  {(h.fromStatus ? (ITEM_STATUS_LABEL[h.fromStatus] ?? h.fromStatus) : '수신') +
                    ' → ' +
                    (ITEM_STATUS_LABEL[h.toStatus] ?? h.toStatus)}
                </td>
                <td>{h.actorType === 'SYSTEM' ? '시스템' : (h.actorName ?? '구성원')}</td>
                <td className="col-muted">{h.reason ?? '—'}</td>
                <td className="col-muted t-data">
                  {h.occurredAt ? new Date(h.occurredAt).toLocaleString('sv-SE').slice(0, 19) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {it.history.length === 0 && (
          <div className="p-6 text-center" style={{ color: 'var(--fg-3)', fontSize: 12 }}>
            기록된 상태 변경 이력이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}
