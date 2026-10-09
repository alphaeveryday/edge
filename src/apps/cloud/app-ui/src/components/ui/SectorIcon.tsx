import { View } from 'react-native';
import Svg, { Circle, Ellipse, G, Path, Rect } from 'react-native-svg';
import { useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';

type Kind = 'chip' | 'bank' | 'game' | 'yield' | 'bond' | 'defense' | 'bio' | 'green' | 'commodity' | 'equity' | 'etc';

export function sectorKind(theme: string): Kind {
  const t = theme;
  if (/반도체|AI|IT|소프트|로봇|테크/.test(t)) return 'chip';
  if (/은행|금융|증권|보험/.test(t)) return 'bank';
  if (/게임|엔터/.test(t)) return 'game';
  if (/배당|인프라|리츠/.test(t)) return 'yield';
  if (/채권|금리/.test(t)) return 'bond';
  if (/방산|국방/.test(t)) return 'defense';
  if (/바이오|헬스|제약/.test(t)) return 'bio';
  if (/친환경|신재생|2차전지|원자력|에너지전환/.test(t)) return 'green';
  if (/원자재|금|에너지|원유/.test(t)) return 'commodity';
  if (/국내주식|해외주식|지수|주식/.test(t)) return 'equity';
  return 'etc';
}

function Glyph({ kind, bg }: { kind: Kind; bg: string }) {
  const W = useColors().onPrimary;
  switch (kind) {
    case 'chip':
      return (
        <G>
          {[8.25, 11.1, 13.95].map((x) => <Rect key={'t' + x} x={x} y={1.6} width={1.8} height={3.6} rx={0.9} fill={W} />)}
          {[8.25, 11.1, 13.95].map((x) => <Rect key={'b' + x} x={x} y={18.8} width={1.8} height={3.6} rx={0.9} fill={W} />)}
          {[8.25, 11.1, 13.95].map((y) => <Rect key={'l' + y} x={1.6} y={y} width={3.6} height={1.8} rx={0.9} fill={W} />)}
          {[8.25, 11.1, 13.95].map((y) => <Rect key={'r' + y} x={18.8} y={y} width={3.6} height={1.8} rx={0.9} fill={W} />)}
          <Rect x={4.6} y={4.6} width={14.8} height={14.8} rx={3.6} fill={W} />
          <Rect x={8.9} y={8.9} width={6.2} height={6.2} rx={1.7} fill={bg} />
        </G>
      );
    case 'bank':
      return (
        <G>
          <Path d="M12 2.4l9.2 4.6v1.9H2.8V7z" fill={W} />
          {[4.2, 8.6, 12.8, 17.2].map((x) => <Rect key={x} x={x} y={10.4} width={2.6} height={6.8} rx={0.8} fill={W} />)}
          <Rect x={2.8} y={18.4} width={18.4} height={2.8} rx={1} fill={W} />
        </G>
      );
    case 'game':
      return (
        <G>
          <Rect x={2.2} y={6.2} width={19.6} height={11.8} rx={5.9} fill={W} />
          <Rect x={5.6} y={11.2} width={5.2} height={1.8} rx={0.9} fill={bg} />
          <Rect x={7.3} y={9.5} width={1.8} height={5.2} rx={0.9} fill={bg} />
          <Circle cx={15.9} cy={10.7} r={1.3} fill={bg} />
          <Circle cx={18} cy={13.4} r={1.3} fill={bg} />
        </G>
      );
    case 'yield':
      return (
        <G>
          <Ellipse cx={12} cy={6.4} rx={7.6} ry={3.1} fill={W} />
          <Path d="M4.4 9.4c0 1.7 3.4 3.1 7.6 3.1s7.6-1.4 7.6-3.1v3.3c0 1.7-3.4 3.1-7.6 3.1s-7.6-1.4-7.6-3.1z" fill={W} />
          <Path d="M4.4 15.4c0 1.7 3.4 3.1 7.6 3.1s7.6-1.4 7.6-3.1v2.4c0 1.7-3.4 3.1-7.6 3.1s-7.6-1.4-7.6-3.1z" fill={W} />
        </G>
      );
    case 'bond':
      return (
        <G>
          <Rect x={3.6} y={3.4} width={16.8} height={17.2} rx={3.2} fill={W} />
          <Rect x={7.2} y={8.2} width={9.6} height={1.9} rx={0.95} fill={bg} />
          <Rect x={7.2} y={11.7} width={9.6} height={1.9} rx={0.95} fill={bg} />
          <Rect x={7.2} y={15.2} width={5.4} height={1.9} rx={0.95} fill={bg} />
        </G>
      );
    case 'defense':
      return (
        <G>
          <Path d="M12 1.9l8.2 3v6.6c0 5-3.3 8.9-8.2 10.6-4.9-1.7-8.2-5.6-8.2-10.6V4.9z" fill={W} />
          <Path d="M8.3 11.9l2.6 2.6 4.8-5.2" fill="none" stroke={bg} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" />
        </G>
      );
    case 'bio':
      return (
        <G>
          <Path d="M8.4 2.2h7.2a1.3 1.3 0 010 2.6h-.5v4l4.6 8.6c1.1 2-.4 4.4-2.6 4.4H6.9c-2.2 0-3.7-2.4-2.6-4.4l4.6-8.6v-4h-.5a1.3 1.3 0 010-2.6z" fill={W} />
          <Circle cx={10.2} cy={17.2} r={1.5} fill={bg} />
          <Circle cx={14.1} cy={14.9} r={1.1} fill={bg} />
        </G>
      );
    case 'green':
      return (
        <G>
          <Path d="M20.6 3.4c.4 9.6-4.4 14.4-11.2 14.4H6.2c-1 0-1.7-.9-1.5-1.9C6.2 8.3 11.5 3.4 20.6 3.4z" fill={W} />
          <Path d="M4.6 20.6c1.2-3.8 3.6-6.8 7.2-8.9" fill="none" stroke={W} strokeWidth={2.2} strokeLinecap="round" />
        </G>
      );
    case 'commodity':
      return (
        <G>
          <Path d="M12 2.2l8.6 4.5v10.6L12 21.8 3.4 17.3V6.7z" fill={W} />
          <Path d="M3.4 6.7L12 11.2l8.6-4.5M12 11.2v10.6" fill="none" stroke={bg} strokeWidth={1.9} strokeLinejoin="round" />
        </G>
      );
    case 'equity':
      return (
        <G>
          <Rect x={3.2} y={12.8} width={4.6} height={8.4} rx={1.8} fill={W} />
          <Rect x={9.7} y={7.4} width={4.6} height={13.8} rx={1.8} fill={W} />
          <Rect x={16.2} y={2.8} width={4.6} height={18.4} rx={1.8} fill={W} />
        </G>
      );
    default:
      return (
        <G>
          <Circle cx={12} cy={12} r={9.4} fill={W} />
          <Path d="M7.6 13.9l3.1-3.4 2.5 2.2 3.4-4" fill="none" stroke={bg} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
        </G>
      );
  }
}

export function SectorIcon({ theme, bg, size = 36 }: { theme: string; bg: string; size?: number }) {
  const i = Math.round(size * 0.56);
  return (
    <View style={{ width: size, height: size, borderRadius: radius.pill, backgroundColor: bg, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={i} height={i} viewBox="0 0 24 24">
        <Glyph kind={sectorKind(theme)} bg={bg} />
      </Svg>
    </View>
  );
}
