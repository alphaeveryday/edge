import { Path } from 'react-native-svg';
import type { ChartData } from '@/api';
import { chgColor } from '@/lib/format';
import { useColors } from '@/theme/theme';
import { ChartFrame } from './ChartFrame';

// 종가 선 차트
export function LineChart({ data, name, price, changePct }: { data: ChartData; name: string; price: number; changePct: number }) {
  const colors = useColors();
  const closes = data.candles.map((c) => c.c);
  return (
    <ChartFrame data={data} name={name} price={price} changePct={changePct} lo={Math.min(...closes)} hi={Math.max(...closes)}>
      {({ x, y }) => (
        <Path d={closes.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')}
          fill="none" stroke={chgColor(colors, changePct)} strokeWidth={2} strokeLinejoin="round" />
      )}
    </ChartFrame>
  );
}
