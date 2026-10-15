import { G, Line, Rect } from 'react-native-svg';
import type { ChartData } from '@/api';
import { useColors } from '@/theme/theme';
import { ChartFrame } from './ChartFrame';

// 시가·고가·저가가 있는 원천용
// 없는 값의 종가 대체
export function CandleChart({ data, name, price, changePct }: { data: ChartData; name: string; price: number; changePct: number }) {
  const colors = useColors();
  const lo = Math.min(...data.candles.map((c) => c.l ?? c.c));
  const hi = Math.max(...data.candles.map((c) => c.h ?? c.c));
  return (
    <ChartFrame data={data} name={name} price={price} changePct={changePct} lo={lo} hi={hi}>
      {({ x, y }) => data.candles.map((c, i) => {
        const o = c.o ?? c.c;
        const col = c.c >= o ? colors.up : colors.down;
        const top = y(Math.max(o, c.c));
        const h = Math.max(1.5, Math.abs(y(o) - y(c.c)));
        return (
          <G key={i}>
            <Line x1={x(i)} x2={x(i)} y1={y(c.h ?? c.c)} y2={y(c.l ?? c.c)} stroke={col} strokeWidth={1} />
            <Rect x={x(i) - 3.5} y={top} width={7} height={h} fill={col} />
          </G>
        );
      })}
    </ChartFrame>
  );
}
