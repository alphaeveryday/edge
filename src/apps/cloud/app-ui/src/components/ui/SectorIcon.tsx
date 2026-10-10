import { View } from 'react-native';
import Svg, { Circle, Ellipse, G, Path, Rect } from 'react-native-svg';
import { useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';

type Kind = 'chip' | 'bank' | 'game' | 'media' | 'yield' | 'bond' | 'defense' | 'bio' | 'power' | 'green' | 'commodity' | 'auto' | 'ship'
  | 'consumer' | 'machine' | 'construction' | 'transport' | 'travel' | 'group' | 'equity' | 'etc';

export function sectorKind(theme: string): Kind {
  const t = theme;
  if (/반도체|AI|IT|소프트|로봇|테크/.test(t)) return 'chip';
  if (/은행|금융|증권|보험/.test(t)) return 'bank';
  if (/게임/.test(t)) return 'game';
  if (/엔터|미디어|콘텐츠|컨텐츠/.test(t)) return 'media';
  // 전력·인프라 테마의 배당 아이콘 오인 방지
  if (/전력|원자력/.test(t)) return 'power';
  if (/배당|인프라|리츠/.test(t)) return 'yield';
  if (/채권|금리/.test(t)) return 'bond';
  if (/방산|국방/.test(t)) return 'defense';
  if (/바이오|헬스|제약/.test(t)) return 'bio';
  if (/친환경|신재생|2차전지|에너지전환/.test(t)) return 'green';
  if (/원자재|금|에너지|원유|소재|철강|화학/.test(t)) return 'commodity';
  if (/자동차|모빌리티/.test(t)) return 'auto';
  if (/조선|해운/.test(t)) return 'ship';
  if (/소비재|화장품|뷰티|푸드/.test(t)) return 'consumer';
  if (/기계|산업재/.test(t)) return 'machine';
  if (/건설/.test(t)) return 'construction';
  if (/운송/.test(t)) return 'transport';
  if (/여행|레저/.test(t)) return 'travel';
  if (/그룹/.test(t)) return 'group';
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
    case 'media':
      return (
        <G>
          <Rect x={2.4} y={4.4} width={19.2} height={15.2} rx={4} fill={W} />
          <Path d="M10.2 8.9v6.2l5.2-3.1z" fill={bg} stroke={bg} strokeWidth={1.2} strokeLinejoin="round" />
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
    case 'power':
      return <Path d="M13.8 1.9L4.6 13.4h6.3l-1.3 8.7 9.8-12h-6.5z" fill={W} stroke={W} strokeWidth={0.8} strokeLinejoin="round" />;
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
    case 'auto':
      return (
        <G>
          <Path d="M6.4 6c.3-1 1.2-1.7 2.3-1.7h6.6c1.1 0 2 .7 2.3 1.7l1.4 4H5z" fill={W} />
          <Rect x={2.6} y={9.2} width={18.8} height={7.4} rx={2.6} fill={W} />
          <Rect x={4.4} y={15.4} width={3.6} height={4.4} rx={1.4} fill={W} />
          <Rect x={16} y={15.4} width={3.6} height={4.4} rx={1.4} fill={W} />
          <Circle cx={6.6} cy={12.9} r={1.3} fill={bg} />
          <Circle cx={17.4} cy={12.9} r={1.3} fill={bg} />
        </G>
      );
    case 'ship':
      return (
        <G>
          <Rect x={10.9} y={2} width={2.2} height={5} rx={1.1} fill={W} />
          <Rect x={6.8} y={6} width={10.4} height={5.4} rx={1.4} fill={W} />
          <Path d="M2.4 12.2h19.2l-2.4 6.6c-.3.9-1.2 1.5-2.1 1.5H6.9c-.9 0-1.8-.6-2.1-1.5z" fill={W} />
          <Circle cx={8.2} cy={15.2} r={1.1} fill={bg} />
          <Circle cx={12} cy={15.2} r={1.1} fill={bg} />
          <Circle cx={15.8} cy={15.2} r={1.1} fill={bg} />
        </G>
      );
    case 'consumer':
      return (
        <G>
          <Path d="M8.6 8.6V6.2a3.4 3.4 0 016.8 0v2.4" fill="none" stroke={W} strokeWidth={2.1} strokeLinecap="round" />
          <Path d="M4.6 7.6h14.8l-1.1 12.1c-.1 1-.9 1.7-1.9 1.7H7.6c-1 0-1.8-.7-1.9-1.7z" fill={W} />
          <Circle cx={8.6} cy={11} r={1.05} fill={bg} />
          <Circle cx={15.4} cy={11} r={1.05} fill={bg} />
        </G>
      );
    case 'machine':
      return (
        <G>
          <Rect x={10.3} y={1.6} width={3.4} height={20.8} rx={1.2} fill={W} />
          <Rect x={1.6} y={10.3} width={20.8} height={3.4} rx={1.2} fill={W} />
          <Path d="M18.15 3.44L20.56 5.85L5.85 20.56L3.44 18.15z M20.56 18.15L18.15 20.56L3.44 5.85L5.85 3.44z" fill={W} />
          <Circle cx={12} cy={12} r={7.2} fill={W} />
          <Circle cx={12} cy={12} r={2.9} fill={bg} />
        </G>
      );
    case 'construction':
      return (
        <G>
          <Path d="M4 14a8 8 0 0116 0z" fill={W} />
          <Rect x={2.2} y={13.6} width={19.6} height={3.4} rx={1.7} fill={W} />
          <Rect x={10.9} y={4.4} width={2.2} height={7} rx={1.1} fill={bg} />
        </G>
      );
    case 'transport':
      return (
        <G>
          <Rect x={1.8} y={4.95} width={12.8} height={11} rx={1.8} fill={W} />
          <Path d="M15.8 8.35h3.1c.5 0 1 .2 1.3.6l2 2.6c.2.3.3.6.3 1v3.4h-6.7z" fill={W} />
          <Circle cx={6.6} cy={17.35} r={2.6} fill={W} stroke={bg} strokeWidth={1.2} />
          <Circle cx={17.6} cy={17.35} r={2.6} fill={W} stroke={bg} strokeWidth={1.2} />
        </G>
      );
    case 'travel':
      return <Path d="M12 1.8c.9 0 1.5.7 1.5 1.6v5.2l7.8 4.7v2.1l-7.8-2.3v5.1l2.2 1.6v1.5L12 20.4l-3.7.9v-1.5l2.2-1.6v-5.1l-7.8 2.3v-2.1l7.8-4.7V3.4c0-.9.6-1.6 1.5-1.6z" fill={W} />;
    case 'group':
      return (
        <G>
          <Rect x={2.8} y={8.4} width={8.4} height={13} rx={1.6} fill={W} />
          <Rect x={12.4} y={2.6} width={8.8} height={18.8} rx={1.6} fill={W} />
          {[11.6, 15.4].map((y) => <Rect key={'l' + y} x={5.6} y={y} width={2.8} height={1.9} rx={0.6} fill={bg} />)}
          {[6, 9.8, 13.6].map((y) => <Rect key={'r' + y} x={15.4} y={y} width={2.8} height={1.9} rx={0.6} fill={bg} />)}
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
  // 원과 짝·홀이 같은 아이콘 크기. 반 픽셀 여백의 쏠림 방지
  const i = size - 2 * Math.round(size * 0.22);
  return (
    <View style={{ width: size, height: size, borderRadius: radius.pill, backgroundColor: bg, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={i} height={i} viewBox="0 0 24 24">
        <Glyph kind={sectorKind(theme)} bg={bg} />
      </Svg>
    </View>
  );
}
