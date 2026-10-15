/** 저장된 계산 근거. 입력·결과는 툴마다 구조가 다르며 그대로 표시한다. */
export interface EvidenceAudit {
  newsId?: string | null;
  toolRunId?: string | null;
  itemIds?: string[] | null;
  asOf?: string | null;
  arguments?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  formulaLatex?: string | null;
  description?: string | null;
}
