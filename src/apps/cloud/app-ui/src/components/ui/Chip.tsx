import { Pressable, Text } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

interface Props {
  label: string;
  on?: boolean;
  variant?: 'filter' | 'add';
  onPress?: () => void;
}

export function Chip({ label, on = false, variant = 'filter', onPress }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const add = variant === 'add';
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.chip, add ? styles.add : on && styles.on, pressed && { opacity: 0.7 }]}>
      {add && (
        <Svg width={12} height={12} viewBox="0 0 14 14">
          <Path d="M7 2v10M2 7h10" stroke={colors.textSub} strokeWidth={1.9} strokeLinecap="round" />
        </Svg>
      )}
      <Text style={[styles.label, { fontFamily: on ? fam.extrabold : fam.semibold, color: add ? colors.textSub : on ? colors.text : colors.textMuted }]}>{label}</Text>
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: radius.control, paddingVertical: 8, paddingHorizontal: 13, borderWidth: 1, borderColor: 'transparent' },
  on: { backgroundColor: colors.surface },
  add: { borderStyle: 'dashed', borderColor: colors.lineStrong },
  label: type.tab,
}));
