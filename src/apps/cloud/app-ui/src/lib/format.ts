export const won = (n: number) => '₩ ' + n.toLocaleString('en-US');
export const pct = (n: number) => (n > 0 ? '+' : '') + n.toFixed(1) + '%';
export const chgColor = (n: number) => (n < 0 ? '#3182F6' : n > 0 ? '#F04452' : '#4E5968');
