import { Pressable, Text } from 'react-native';
import { createStyles, useColors } from '@/theme/theme';
import { radius, type Palette } from '@/theme/tokens';
import { type } from '@/theme/typography';

type Tone = 'primary' | 'dark' | 'danger' | 'soft';
// dark 는 본문색 바탕의 반전 버튼
const tones = (c: Palette): Record<Tone | 'disabled', [string, string]> => ({
  primary: [c.primary, c.onPrimary],
  dark: [c.text, c.bg],
  danger: [c.up, c.onPrimary],
  soft: [c.surface, c.textSub],
  disabled: [c.surface, c.textDisabled],
});

interface Props {
  label: string;
  tone?: Tone;
  size?: 'md' | 'sm';
  disabled?: boolean;
  grow?: boolean;
  onPress?: () => void;
}

export function CtaButton({ label, tone = 'primary', size = 'md', disabled, grow, onPress }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const [bg, fg] = tones(colors)[disabled ? 'disabled' : tone];
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      style={({ pressed }) => [styles.btn, { height: size === 'sm' ? 46 : 54, backgroundColor: bg, flex: grow ? 1 : undefined }, pressed && !disabled && { transform: [{ scale: 0.985 }] }]}
    >
      <Text style={[styles.label, { color: fg }]}>{label}</Text>
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  btn: { borderRadius: radius.button, alignItems: 'center', justifyContent: 'center', alignSelf: 'stretch' },
  label: type.button,
}));
