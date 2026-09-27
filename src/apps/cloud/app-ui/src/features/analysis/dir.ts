import type { Dir } from '@/api';
import type { Signal } from '@/theme/tokens';

export const dirSignal: Record<Dir, Signal> = { help: 'up', neutral: 'neutral', burden: 'down' };
export const dirLabel: Record<Dir, string> = { help: '도움', neutral: '중립', burden: '부담' };
