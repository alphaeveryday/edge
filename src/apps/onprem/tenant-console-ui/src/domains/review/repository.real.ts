/* review 도메인 — tenant-console-api 검수 실계약 연동. 검수 표면은 snake_case
 * (ALPHA-437 규약)라 이 계층에서 camelCase 로 투영한다. null 필드는 와이어에서
 * 생략되므로(NON_NULL) ?? 로 보정한다. */
import { apiClient } from '../../api/client';
import type { ReviewRepository } from './repository';
import type { ReviewItem, ReviewItemDetail } from './types';

interface WireItem {
  explanation_result_id: string;
  etf_ticker?: string;
  etf_name?: string;
  trade_date?: string;
  summary: string;
  headline?: string;
  confidence_level?: string;
  status: string;
  received_at?: string;
  review_reasons?: string[];
  gate_checks?: { matched_text?: string; min_source_count?: number; min_confidence?: string }[];
}

interface WireDetail extends WireItem {
  explanation_as_of?: string;
  content_as_of?: string;
  evidences: { kind: string; title: string | null; source: string; published_at?: string; source_uri?: string }[];
  checks: {
    result: 'PASS' | 'REVIEW' | 'BLOCK';
    rule_type?: string;
    matched_text?: string;
    checked_at?: string;
    policy_version_no?: number;
    min_source_count?: number;
    min_confidence?: string;
  }[];
  history: {
    from_status?: string;
    to_status: string;
    actor_type: 'SYSTEM' | 'MEMBER';
    actor_name?: string;
    reason?: string;
    occurred_at?: string;
  }[];
}

function toItem(w: WireItem): ReviewItem {
  return {
    id: w.explanation_result_id,
    ticker: w.etf_ticker ?? null,
    name: w.etf_name ?? null,
    tradeDate: w.trade_date ?? null,
    summary: w.summary,
    headline: w.headline ?? null,
    confidenceLevel: w.confidence_level ?? null,
    status: w.status,
    receivedAt: w.received_at ?? null,
    reviewReasons: w.review_reasons ?? [],
    gateChecks: (w.gate_checks ?? []).map((g) => ({
      matchedText: g.matched_text ?? null,
      minSourceCount: g.min_source_count ?? null,
      minConfidence: g.min_confidence ?? null,
    })),
  };
}

function toDetail(w: WireDetail): ReviewItemDetail {
  return {
    ...toItem(w),
    explanationAsOf: w.explanation_as_of ?? null,
    contentAsOf: w.content_as_of ?? null,
    evidences: (w.evidences ?? []).map((e) => ({
      kind: e.kind,
      // title 은 계약상 nullable — null 이면 링크 앵커가 빈 셀이 되므로 설명 상세와 같은 폴백.
      title: e.title ?? '(제목 없음)',
      source: e.source,
      publishedAt: e.published_at ?? null,
      sourceUri: e.source_uri ?? null,
    })),
    checks: (w.checks ?? []).map((c) => ({
      result: c.result,
      ruleType: c.rule_type ?? null,
      matchedText: c.matched_text ?? null,
      policyVersionNo: c.policy_version_no ?? null,
      minSourceCount: c.min_source_count ?? null,
      minConfidence: c.min_confidence ?? null,
      checkedAt: c.checked_at ?? null,
    })),
    history: (w.history ?? []).map((h) => ({
      fromStatus: h.from_status ?? null,
      toStatus: h.to_status,
      actorType: h.actor_type,
      actorName: h.actor_name ?? null,
      reason: h.reason ?? null,
      occurredAt: h.occurred_at ?? null,
    })),
  };
}

export const realReviewRepository: ReviewRepository = {
  listPending: ({ limit, offset }) =>
    apiClient
      .get<WireItem[]>(`/review/items?status=REVIEW_REQUIRED&limit=${limit}&offset=${offset}`)
      .then((r) => r.map(toItem)),
  detail: (id) => apiClient.get<WireDetail>(`/review/items/${encodeURIComponent(id)}`).then(toDetail),
  approve: (id, note) => apiClient.post<void>(`/review/items/${encodeURIComponent(id)}/approve`, { note }),
  approveEdited: (id, editedSummary, note) =>
    apiClient.post<void>(`/review/items/${encodeURIComponent(id)}/approve-edited`, { edited_summary: editedSummary, note }),
  reject: (id, reason) => apiClient.post<void>(`/review/items/${encodeURIComponent(id)}/reject`, { reason }),
  block: (id, reason) => apiClient.post<void>(`/review/items/${encodeURIComponent(id)}/block`, { reason }),
};
