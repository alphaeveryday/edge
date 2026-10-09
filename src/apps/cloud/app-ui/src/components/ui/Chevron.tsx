import Svg, { Path } from 'react-native-svg';
import { useColors } from '@/theme/theme';

const D = { right: 'M5 3l4 4-4 4', down: 'M3 5l4 4 4-4', up: 'M3 9l4-4 4 4', left: 'M9 3L5 7l4 4' } as const;

export function Chevron({ size = 14, color, dir = 'right' }: { size?: number; color?: string; dir?: keyof typeof D }) {
  const colors = useColors();
  return (
    <Svg width={size} height={size} viewBox="0 0 14 14">
      <Path d={D[dir]} stroke={color ?? colors.text} strokeWidth={1.9} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}
