import type { ColorValue } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';

type P = { color: ColorValue; size?: number };
const st = (color: ColorValue) => ({ stroke: color, strokeWidth: 1.9, fill: 'none' as const, strokeLinejoin: 'round' as const });

export const HomeIcon = ({ color, size = 24 }: P) => (
  <Svg width={size} height={size} viewBox="0 0 24 24"><Path d="M4.5 11.5L12 5l7.5 6.5V19h-5.2v-4.8H9.7V19H4.5z" {...st(color)} /></Svg>
);
export const WatchIcon = ({ color, size = 24 }: P) => (
  <Svg width={size} height={size} viewBox="0 0 24 24"><Path d="M12 19.6S4.2 14.4 4.2 9.3A3.9 3.9 0 0112 6.9a3.9 3.9 0 017.8 2.4c0 5.1-7.8 10.3-7.8 10.3z" {...st(color)} /></Svg>
);
export const ExploreIcon = ({ color, size = 24 }: P) => (
  <Svg width={size} height={size} viewBox="0 0 24 24"><Circle cx={12} cy={12} r={8} {...st(color)} /><Path d="M15 9l-2.2 5.2L7.6 16l2.2-5.2z" {...st(color)} strokeWidth={1.7} /></Svg>
);
export const CommunityIcon = ({ color, size = 24 }: P) => (
  <Svg width={size} height={size} viewBox="0 0 24 24"><Path d="M20 12.4c0 3.2-3.6 5.8-8 5.8-1 0-2-.14-2.9-.4L5 19.5l.9-3.1C4.7 15.3 4 13.9 4 12.4 4 9.2 7.6 6.6 12 6.6s8 2.6 8 5.8z" {...st(color)} /></Svg>
);
