import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import { colors, radius, shadow } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export type IconName = 'back' | 'close' | 'search' | 'bell' | 'menu' | 'plus' | 'share' | 'prev' | 'next';

function Icon({ name, c, big }: { name: IconName; c: string; big: boolean }) {
  const st = { stroke: c, fill: 'none' as const, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  switch (name) {
    case 'back':
      return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M12.5 4.5L6 10l6.5 5.5" {...st} strokeWidth={2} /></Svg>;
    case 'close':
      return <Svg width={18} height={18} viewBox="0 0 18 18"><Path d="M4 4l10 10M14 4L4 14" {...st} strokeWidth={2} /></Svg>;
    case 'search':
      return <Svg width={20} height={20} viewBox="0 0 20 20"><Circle cx={9} cy={9} r={6} {...st} strokeWidth={1.9} /><Path d="M13.5 13.5l4 4" {...st} strokeWidth={1.9} /></Svg>;
    case 'bell':
      return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M10 3a4.5 4.5 0 00-4.5 4.5v2.7L4 13h12l-1.5-2.8V7.5A4.5 4.5 0 0010 3z" {...st} strokeWidth={1.8} /><Path d="M8.2 15.5a1.9 1.9 0 003.6 0" {...st} strokeWidth={1.8} /></Svg>;
    case 'menu':
      return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M3.5 6h13M3.5 10h13M3.5 14h13" {...st} strokeWidth={1.9} /></Svg>;
    case 'plus': {
      const s = big ? 24 : 18;
      return <Svg width={s} height={s} viewBox="0 0 18 18"><Path d="M9 3v12M3 9h12" {...st} strokeWidth={2} /></Svg>;
    }
    case 'share':
      return <Svg width={20} height={20} viewBox="0 0 20 20"><Path d="M10 12V3M6.5 6L10 2.5 13.5 6M4.5 10v7h11v-7" {...st} strokeWidth={1.7} /></Svg>;
    case 'prev':
      return <Svg width={14} height={14} viewBox="0 0 14 14"><Path d="M8.5 2.5L4 7l4.5 4.5" {...st} strokeWidth={2} /></Svg>;
    case 'next':
      return <Svg width={14} height={14} viewBox="0 0 14 14"><Path d="M5.5 2.5L10 7l-4.5 4.5" {...st} strokeWidth={2} /></Svg>;
  }
}

interface Props {
  icon: IconName;
  size?: number;
  circled?: boolean;
  floating?: boolean;
  fab?: boolean;
  soft?: boolean;
  color?: string;
  badge?: string | number;
  onPress?: () => void;
}

export function IconButton({ icon, size = 38, circled, floating, fab, soft, color = colors.text, badge, onPress }: Props) {
  const c = fab ? colors.white : color;
  const bg = fab ? colors.text : floating ? 'rgba(242,244,246,0.94)' : soft ? colors.surface : circled ? colors.white : 'transparent';
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={icon}
      hitSlop={6}
      style={({ pressed }) => [
        styles.btn,
        { width: size, height: size, backgroundColor: bg, borderWidth: circled ? 1 : 0 },
        fab && styles.fabShadow,
        floating && styles.floatShadow,
        pressed && { opacity: 0.7 },
      ]}
    >
      <Icon name={icon} c={c} big={!!fab} />
      {badge !== undefined && badge !== '' && (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{badge}</Text>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: { borderRadius: radius.pill, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  fabShadow: shadow.fab,
  floatShadow: shadow.floating,
  badge: { position: 'absolute', top: -2, right: -2, minWidth: 17, height: 17, paddingHorizontal: 4, borderRadius: radius.pill, backgroundColor: colors.up, borderWidth: 2, borderColor: colors.white, alignItems: 'center', justifyContent: 'center' },
  badgeText: { fontFamily: fam.extrabold, fontSize: 10.5, color: colors.white },
});
