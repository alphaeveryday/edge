// 3단계 API 계약과 1:1 로 맞출 응답 타입. 지금은 화면 뼈대에 필요한 최소만.
import type { Signal } from '@/theme/tokens';

export type EtfCode = string;

export interface EtfSummary {
  code: EtfCode;
  name: string;
  theme: string;
  price: number;
  changePct: number;
  signal: Signal;
}
