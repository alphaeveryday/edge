import { Pressable, Text, View } from 'react-native';
import { Icon, type IconName as Glyph } from './Icon';
import { createStyles, useColors } from '@/theme/theme';
import { radius, shadow } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export type IconName = 'back' | 'close' | 'search' | 'bell' | 'menu' | 'plus' | 'share' | 'prev' | 'next';

const GLYPH: Record<IconName, [Glyph, number]> = {
  back: ['chevron-left', 22],
  close: ['x', 20],
  search: ['search', 20],
  bell: ['bell', 19],
  menu: ['menu', 20],
  plus: ['plus', 21],
  share: ['share', 19],
  prev: ['chevron-left', 18],
  next: ['chevron-right', 18],
};

interface Props {
  icon: IconName;
  size?: number;
  circled?: boolean;
  fab?: boolean;
  soft?: boolean;
  color?: string;
  badge?: string | number;
  onPress?: () => void;
  disabled?: boolean;
}

export function IconButton({ icon, size = 38, circled, fab, soft, color, badge, onPress, disabled }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const c = fab ? colors.bg : color ?? colors.text;
  const bg = fab ? colors.text : soft ? colors.surface : circled ? colors.bg : 'transparent';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={icon}
      hitSlop={6}
      style={({ pressed }) => [
        styles.btn,
        { width: size, height: size, backgroundColor: bg, borderWidth: circled ? 1 : 0 },
        fab && styles.fabShadow,
        pressed && { opacity: 0.7 },
      ]}
    >
      <Icon name={GLYPH[icon][0]} color={c} size={fab ? 27 : GLYPH[icon][1]} />
      {badge !== undefined && badge !== '' && (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{badge}</Text>
        </View>
      )}
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  btn: { borderRadius: radius.pill, borderColor: colors.lineStrong, alignItems: 'center', justifyContent: 'center' },
  fabShadow: shadow.fab,
  badge: { position: 'absolute', top: -2, right: -2, minWidth: 17, height: 17, paddingHorizontal: 4, borderRadius: radius.pill, backgroundColor: colors.up, borderWidth: 2, borderColor: colors.bg, alignItems: 'center', justifyContent: 'center' },
  badgeText: { fontFamily: fam.extrabold, fontSize: 10.5, color: colors.onPrimary },
}));
