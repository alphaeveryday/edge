import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Path, Rect } from 'react-native-svg';
import { Chevron } from './Chevron';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export type ListIcon = 'chart' | 'issue' | 'star' | 'search' | 'theme' | 'comm' | 'bell';
const C = '#333D4B';

function Icon({ name }: { name: ListIcon }) {
  const st = { stroke: C, fill: 'none' as const, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  switch (name) {
    case 'chart': return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M3 15.5l4.5-5 3.5 3 6-7.5" {...st} strokeWidth={1.8} /></Svg>;
    case 'issue': return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M4 4h12v12H4z" {...st} strokeWidth={1.7} /><Path d="M7 8h6M7 11h6M7 14h3" {...st} strokeWidth={1.7} /></Svg>;
    case 'star': return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M10 2.8l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8.1l5-.7z" {...st} strokeWidth={1.7} /></Svg>;
    case 'search': return <Svg width={20} height={20} viewBox="0 0 20 20"><Circle cx={9} cy={9} r={5.5} {...st} strokeWidth={1.8} /><Path d="M13.2 13.2l3.5 3.5" {...st} strokeWidth={1.8} /></Svg>;
    case 'theme': return <Svg width={20} height={20} viewBox="0 0 20 20">{[[3, 3], [11, 3], [3, 11], [11, 11]].map(([x, y]) => <Rect key={`${x}${y}`} x={x} y={y} width={6} height={6} rx={1.5} {...st} strokeWidth={1.7} />)}</Svg>;
    case 'comm': return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M17 10.4c0 2.9-3.1 5.2-7 5.2-.9 0-1.7-.1-2.5-.4L4 16.5l.8-2.7C3.7 12.9 3 11.7 3 10.4 3 7.5 6.1 5.2 10 5.2s7 2.3 7 5.2z" {...st} strokeWidth={1.7} /></Svg>;
    case 'bell': return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M10 3a4.5 4.5 0 00-4.5 4.5v2.7L4 13h12l-1.5-2.8V7.5A4.5 4.5 0 0010 3z" {...st} strokeWidth={1.7} /><Path d="M8.2 15.5a1.9 1.9 0 003.6 0" {...st} strokeWidth={1.7} /></Svg>;
  }
}

interface Props {
  label: string;
  icon?: ListIcon;
  sub?: string;
  value?: string;
  labelColor?: string;
  chevron?: boolean;
  divider?: boolean;
  onPress?: () => void;
}

export function ListRow({ label, icon, sub, value, labelColor = colors.text, chevron = true, divider, onPress }: Props) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.row, divider && styles.divider, pressed && { opacity: 0.6 }]}>
      {icon && <View style={styles.icon}><Icon name={icon} /></View>}
      <View style={{ flex: 1, gap: 3 }}>
        <Text numberOfLines={1} style={[styles.label, { color: labelColor }]}>{label}</Text>
        {!!sub && <Text numberOfLines={1} style={styles.sub}>{sub}</Text>}
      </View>
      {!!value && <Text style={styles.value}>{value}</Text>}
      {chevron && <Chevron size={16} color={colors.textDisabled} />}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 8 },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  icon: { width: 22, height: 22, alignItems: 'center', justifyContent: 'center' },
  label: { fontFamily: fam.semibold, fontSize: 15.5, letterSpacing: -0.3 },
  sub: { fontFamily: fam.regular, fontSize: 12.5, color: colors.textMuted },
  value: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
});
