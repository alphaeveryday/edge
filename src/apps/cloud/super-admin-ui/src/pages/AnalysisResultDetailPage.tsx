/* 분석 결과 상세 — 새 분석 엔진(v2) 분석 한 건 (ALPHA-1333).
 *
 * 저장된 내용을 그대로 보여 준다 — 문장을 다듬거나 항목을 고르지 않는다. 가격변동 분석은 고르지 않은
 * 항목도 함께 나오고, 고른 항목에 순번을 붙인다.
 */
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { AnalysisKind, MovementDetail, OutlookDetail, OutlookItem } from '../domains/analysisResults';
import { useAnalysisResult } from '../domains/analysisResults/hooks';
import { KIND_LABEL, STATUS_TONE, publicationText } from './AnalysisResultsPage';
import { STATE_LABEL } from './ops/etfAnalysisView';
import { kst } from './ops/shared';
import { LoadError } from './_shared/LoadError';

const SENTIMENT_LABEL: Record<string, string> = { positive: '긍정', neutral: '중립', negative: '부정' };
const sentiment = (s: string | null) => (s ? (SENTIMENT_LABEL[s] ?? s) : '—');
/** JSON 열은 저장된 값을 그대로 적는다 — 문자열은 문자열로, 나머지는 JSON 으로 */
const raw = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : v == null ? [] : [v]);

export function AnalysisResultDetailPage() {
  const { kind: kindParam = '', id = '' } = useParams();
  const kind: AnalysisKind = kindParam === 'movement' ? 'movement' : 'outlook';
  const { data: d, isPending, isError, error } = useAnalysisResult(kind, id);

  if (isPending) return <PageSkeleton rows={6} />;
  if (isError) return <LoadError error={error} />;

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
      <Section title={`${KIND_LABEL[d.kind]} · ${d.etfName} (${d.etfCode})`}>
        <Fields
          rows={[
            ['분석 id', <span className="mono">{d.analysisId}</span>],
            ['분석 시각', kst(d.analysisAt)],
            ['상태', <StatusBadge tone={STATUS_TONE[d.status]}>{STATE_LABEL[d.status]}</StatusBadge>],
            [
              '게시',
              publicationText({
                published: d.published,
                publishedAt: d.publishedAt,
                withdrawn: d.movement ? d.movement.withdrawn : null,
              }),
            ],
            ['입력 출처', d.dataSource],
            [
              '직전 분석',
              d.previousAnalysisId ? (
                <Link className="mono" to={`/analysis-results/${d.kind}/${encodeURIComponent(d.previousAnalysisId)}`}>
                  {d.previousAnalysisId}
                </Link>
              ) : (
                '—'
              ),
            ],
          ]}
        />
        {d.errorMessage && (
          <pre className="card-pad t-xs m-0 whitespace-pre-wrap" style={{ color: 'var(--down)' }}>
            {d.errorMessage}
          </pre>
        )}
      </Section>
      {d.outlook && <Outlook o={d.outlook} />}
      {d.movement && <Movement m={d.movement} />}
    </div>
  );
}

function Outlook({ o }: { o: OutlookDetail }) {
  const detail = o.items.filter((i) => i.section === 'detail');
  const update = o.items.filter((i) => i.section === 'update');
  return (
    <>
      <Section title="요약">
        <Fields
          rows={[
            ['스티커', o.sticker ?? '—'],
            ['요약 제목', o.summaryTitle ?? '—'],
            ['요약', <Text v={o.summary} />],
          ]}
        />
      </Section>
      <Section title={`상세 — ${o.detailTitle ?? '제목 없음'} (${o.detailMode ?? '—'})`}>
        <Items items={detail} />
        {update.length > 0 && (
          <>
            <div className="card-pad t-label" style={{ paddingBottom: 0 }}>
              갱신 항목
            </div>
            <Items items={update} />
          </>
        )}
      </Section>
      <Section title="요인">
        <table className="table">
          <thead>
            <tr>
              <th>유형</th>
              <th>스티커</th>
              <th>문장</th>
            </tr>
          </thead>
          <tbody>
            {o.factors.map((f) => (
              <tr key={f.type}>
                <td>{f.type}</td>
                <td>{f.sticker}</td>
                <td className="t-xs">{f.sentence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title={`결론 — ${o.conclusionTitle ?? '제목 없음'}`}>
        <Fields
          rows={[
            ['결론 문장', <Text v={o.conclusionSentence} />],
            ['지지 키워드', keywords(o, 'support')],
            ['부담 키워드', keywords(o, 'burden')],
            ['전망이 바뀌는 조건', <Text v={o.changeCondition} />],
          ]}
        />
      </Section>
    </>
  );
}

const keywords = (o: OutlookDetail, kind: string) =>
  o.conclusionKeywords
    .filter((k) => k.kind === kind)
    .map((k) => k.label)
    .join(', ') || '—';

function Items({ items }: { items: OutlookItem[] }) {
  if (items.length === 0) return <p className="card-pad t-xs m-0">항목 없음</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>#</th>
          <th>키워드</th>
          <th>내용</th>
          <th>감성</th>
          <th>출처</th>
        </tr>
      </thead>
      <tbody>
        {items.map((i) => (
          <tr key={`${i.section}-${i.itemId}`}>
            <td className="num">{i.position + 1}</td>
            <td>
              {i.titleKeyword}
              {i.changeType && <span className="tag ml-1">{i.changeType}</span>}
            </td>
            <td className="t-xs">
              {list(i.bullets).map((b, n) => (
                <div key={n}>· {raw(b)}</div>
              ))}
              {i.sentence && <div>{i.sentence}</div>}
            </td>
            <td className="t-xs">{sentiment(i.sentiment)}</td>
            <td className="t-xs mono" style={{ wordBreak: 'break-all' }}>
              {list(i.sourceLinks).map((s, n) => (
                <div key={n}>{raw(s)}</div>
              ))}
              {i.sourceAsOf && <div>기준 {kst(i.sourceAsOf)}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Movement({ m }: { m: MovementDetail }) {
  return (
    <>
      <Section title={`요약 — 거래일 ${m.tradingDate}`}>
        <Fields
          rows={[
            ['요약', <Text v={m.summary} />],
            ['회수', m.withdrawn ? `회수됨 ${kst(m.withdrawnAt)}` : '회수 안 됨'],
          ]}
        />
      </Section>
      <Section title={`항목 — 저장 ${m.items.length}건 · 고른 항목 ${m.selectedItemIds.length}건`}>
        <table className="table">
          <thead>
            <tr>
              <th>고른 순서</th>
              <th>유형</th>
              <th>키워드</th>
              <th>근거</th>
              <th>감성</th>
              <th>기준 시각</th>
            </tr>
          </thead>
          <tbody>
            {m.items.map((i) => {
              const order = m.selectedItemIds.indexOf(i.itemId);
              return (
                <tr key={i.itemId}>
                  <td className="num">{order >= 0 ? order + 1 : '—'}</td>
                  <td>{i.type}</td>
                  <td>{i.titleKeyword}</td>
                  <td className="t-xs">{i.sentence}</td>
                  <td className="t-xs">{sentiment(i.sentiment)}</td>
                  <td className="t-xs whitespace-nowrap">{kst(i.sourceAsOf)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Section>
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card overflow-x-auto">
      <div className="card-head">
        <span className="t-label">{title}</span>
      </div>
      {children}
    </div>
  );
}

function Fields({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <table className="table">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <th className="whitespace-nowrap" style={{ width: 160 }}>
              {label}
            </th>
            <td>{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const Text = ({ v }: { v: string | null }) => <span className="whitespace-pre-wrap">{v ?? '—'}</span>;
