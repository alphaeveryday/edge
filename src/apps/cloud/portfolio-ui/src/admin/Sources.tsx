import { safeReportUrl } from '../reportUrl';

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
const text = (value: unknown) => typeof value === 'string' ? value : '';
const labels: Record<string, string> = { sections: '보고서', question: '질문 해석', portfolio: '구성 분석', outlook: '전망', opinion: '최종 의견', current_evaluation: '현재 평가', recommendation: '추천', evaluation: '평가', reason: '이유', insights: '인사이트', content: '내용', dividend_observations: '배당 자료', answer: '추가 답변' };

function quotes(value: unknown, path = ''): { location: string; item: Record<string, unknown> }[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => quotes(item, `${path} ${i + 1}`));
  return Object.entries(object(value)).flatMap(([key, item]) => key === 'evidence' && Array.isArray(item)
    ? item.map(entry => ({ location: path, item: object(entry) }))
    : quotes(item, [path, labels[key] || key].filter(Boolean).join(' · ')));
}

function Link({ url }: { url: unknown }) {
  const value = text(url), href = safeReportUrl(value);
  return href ? <a href={href} target="_blank" rel="noreferrer">{value}</a> : <span>{value || '원문 주소 없음'}</span>;
}

export function Sources({ result, followups = [] }: { result?: Record<string, unknown> | null; followups?: { result: unknown }[] }) {
  const sources = Array.isArray(result?.sources) ? result.sources.map(object) : [];
  const evidence = [...quotes(result), ...followups.flatMap((item, i) => quotes(item.result, `추가 질문 ${i + 1}`))];
  return <>
    <h3>출처 목록</h3>
    {sources.length ? sources.map((source, i) => <article className="source-record" key={i}>
      <h4>{text(source.title) || text(source.id) || '출처'}</h4>
      {text(source.id) && <p>출처 ID · <code>{text(source.id)}</code></p>}
      <Link url={source.url} />
      {text(source.fetched_at) && <p className="muted">수집 시각 · {text(source.fetched_at)}</p>}
    </article>) : <p className="muted">저장된 출처가 없습니다.</p>}
    <h3>인용문</h3>
    {evidence.length ? evidence.map(({ location, item }, i) => {
      const source = sources.find(s => text(s.id) && s.id === item.source_id);
      const quoteId = text(item.passage_id);
      return <article className="source-record" key={i}><h4>{location}</h4>
        <blockquote>{text(item.quote) || '저장된 인용문 없음'}</blockquote>
        {text(item.source_id) && <p>출처 ID · <code>{text(item.source_id)}</code></p>}
        {quoteId && <p>인용 ID · <code>{quoteId}</code></p>}
        {source ? <Link url={source.url} /> : text(item.url) ? <Link url={item.url} /> : <p className="muted">출처 정보 없음</p>}
      </article>;
    }) : <p className="muted">저장된 인용문이 없습니다.</p>}
  </>;
}
