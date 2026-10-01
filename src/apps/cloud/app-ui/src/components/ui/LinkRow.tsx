import { Pressable, StyleSheet, Text } from 'react-native';
import { Chevron } from './Chevron';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  label: string;
  variant?: 'inline' | 'card' | 'accent';
  open?: boolean | null;
  muted?: boolean;
  center?: boolean;
  divider?: boolean;
  onPress?: () => void;
}

export function LinkRow({ label, variant = 'inline', open = null, muted, center, divider, onPress }: Props) {
  const accent = variant === 'accent';
  const card = variant === 'card' || accent;
  const centered = !!center || card;
  const c = accent ? colors.downDeep : muted ? colors.textSub : colors.text;
  const dir = open === true ? 'up' : open === false ? 'down' : 'right';
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        { justifyContent: centered ? 'center' : 'flex-start', backgroundColor: accent ? 'rgba(27,100,218,0.09)' : card ? colors.card : 'transparent', borderRadius: card ? radius.button : 0, paddingHorizontal: card ? 16 : 0 },
        divider && styles.divider,
        pressed && { opacity: 0.7 },
      ]}
    >
      <Text numberOfLines={1} style={[styles.label, { color: c, fontFamily: muted ? fam.bold : fam.extrabold }, !centered && { flex: 1 }]}>{label}</Text>
      <Chevron size={14} color={c} dir={dir} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 44 },
  divider: { borderTopWidth: 1, borderTopColor: colors.surface },
  label: { fontSize: 14, letterSpacing: -0.28 },
});
