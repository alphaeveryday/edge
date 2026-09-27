import type { ApiClient } from '../client';
import type { EtfSummary } from '../types';

const ETFS: EtfSummary[] = [
  { code: 'AXAI', name: 'TIGER 반도체TOP10', theme: 'AI·반도체', price: 12845, changePct: 3.2, signal: 'strongUp' },
  { code: 'DEFN', name: 'PLUS K방산', theme: '방산', price: 21416, changePct: 1.8, signal: 'strongUp' },
  { code: 'GRID', name: 'KODEX 미국AI전력핵심인프라', theme: '전력 인프라', price: 15366, changePct: 0.9, signal: 'up' },
];

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

export const mockClient: ApiClient = {
  etf: {
    listWatch: () => delay(ETFS),
    get: (code) => {
      const e = ETFS.find((x) => x.code === code);
      return e ? delay(e) : Promise.reject(new Error(`unknown etf ${code}`));
    },
  },
};
