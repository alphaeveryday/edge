import { useState } from 'react';
import { Icon, Toggle, toast } from 'ui-kit';
import type { Market } from '../domains/explanations';
import { useMarketScopes, useScopeActions, useStockScopes } from '../domains/scope/hooks';
import { LoadError } from './_shared/cells';

const MARKET_TITLE: Record<Market, { name: string; sub: string; desc: string }> = {
  KRX: { name: 'KRX', sub: '한국거래소', desc: '국내 상장 종목의 가격 변동 설명을 제공합니다.' },
  NASDAQ: { name: 'NASDAQ', sub: '미국 나스닥', desc: '미국 상장 종목의 가격 변동 설명을 제공합니다.' },
};

export function ScopePage() {
  const marketsQuery = useMarketScopes();
  const stocksQuery = useStockScopes();
  const { toggleMarket, toggleStock } = useScopeActions();

  const [q, setQ] = useState('');

  if (marketsQuery.isError || stocksQuery.isError) return <LoadError />;
  // 시장 상태를 모르는 채 종목 토글을 그리면 비활성 시장이 활성으로 보인다 — 둘 다 로드된 뒤 렌더
  if (marketsQuery.isPending || stocksQuery.isPending) return null;

  const markets = marketsQuery.data;
  const stocks = stocksQuery.data;
  const marketEnabled = (market: Market) => markets.find((m) => m.market === market)?.enabled ?? false;
  const keyword = q.trim().toLowerCase();
  const filtered = stocks.filter(
    (s) => !keyword || s.name.toLowerCase().includes(keyword) || s.code.toLowerCase().includes(keyword),
  );

  return (
    <div className="flex max-w-[860px] flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">시장</span>
        </div>
        <div className="p-4">
          {markets.map((m, i) => (
            <div
              key={m.market}
              className="flex items-center gap-3 py-2.5"
              style={i < markets.length - 1 ? { borderBottom: '1px solid var(--border-faint)' } : undefined}
            >
              <div className="flex-1">
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  {MARKET_TITLE[m.market].name}{' '}
                  <span style={{ fontWeight: 400, color: 'var(--fg-4)', fontSize: 12 }}>
                    {MARKET_TITLE[m.market].sub}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>
                  {MARKET_TITLE[m.market].desc}
                </div>
              </div>
              <span className="num" style={{ fontSize: 11, color: 'var(--fg-3)' }}>
                {m.stockCount}종목
              </span>
              <Toggle on={m.enabled} onToggle={() => toggleMarket.mutate(m.market)} aria-label={`${m.market} 제공 여부`} />
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">종목별 제공 설정</span>
          <label className="field" style={{ width: 240, height: 26 }}>
            <Icon name="search" className="ic" />
            <input placeholder="종목 검색" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>종목</th>
              <th style={{ width: 96 }}>시장</th>
              <th style={{ width: 96 }}>제공 여부</th>
              <th className="col-muted" style={{ width: 160 }}>비고</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const mOn = marketEnabled(s.market);
              const on = mOn && s.enabled;
              return (
                <tr key={s.code} style={{ opacity: on ? 1 : 0.45 }}>
                  <td>
                    <span className="font-semibold">{s.name}</span>{' '}
                    <span className="num" style={{ color: 'var(--fg-4)', fontSize: 12 }}>
                      {s.code}
                    </span>
                  </td>
                  <td className="col-muted">{s.market}</td>
                  <td>
                    <Toggle
                      on={on}
                      disabled={!mOn}
                      onToggle={() => {
                        if (!mOn) {
                          toast(`${s.market} 시장이 비활성 상태입니다.`);
                          return;
                        }
                        toggleStock.mutate(s.code);
                      }}
                      aria-label={`${s.name} 제공 여부`}
                    />
                  </td>
                  <td className="col-muted" style={{ fontSize: 11 }}>
                    {!mOn ? `${s.market} 시장 비활성` : s.enabled ? '' : '종목 단위 제외'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
