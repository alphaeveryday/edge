import Svg, { Path } from 'react-native-svg';

const D = { right: 'M5 3l4 4-4 4', down: 'M3 5l4 4 4-4', up: 'M3 9l4-4 4 4', left: 'M9 3L5 7l4 4' } as const;

export function Chevron({ size = 14, color = '#191F28', dir = 'right' }: { size?: number; color?: string; dir?: keyof typeof D }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 14 14">
      <Path d={D[dir]} stroke={color} strokeWidth={1.9} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}
