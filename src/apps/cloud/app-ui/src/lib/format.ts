import { colors } from '@/theme/tokens';

export const won = (n: number) => '₩ ' + n.toLocaleString('en-US');
export const pct = (n: number) => (n > 0 ? '+' : '') + n.toFixed(1) + '%';
export const chgColor = (n: number) => (n < 0 ? colors.down : n > 0 ? colors.up : colors.textSub);
