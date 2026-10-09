import { Pressable, Text, View } from 'react-native';
import { BottomSheet, SheetHead } from '@/components/ui';
import { CheckCircle } from '@/features/watch/CheckCircle';
import { createStyles, useThemePref, type ThemePref } from '@/theme/theme';
import { type } from '@/theme/typography';

export const THEME_LABEL: Record<ThemePref, string> = { system: '시스템 설정', light: '라이트', dark: '다크' };
const OPTIONS: { k: ThemePref; label: string }[] = [
  { k: 'system', label: '시스템 설정 따름' },
  { k: 'light', label: '라이트' },
  { k: 'dark', label: '다크' },
];

// 고르는 즉시 적용하고 닫는 화면 모드 시트
export function ThemeSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const styles = useStyles();
  const pref = useThemePref((s) => s.pref);
  const setPref = useThemePref((s) => s.setPref);
  const pick = (k: ThemePref) => {
    setPref(k);
    onClose();
  };
  return (
    <BottomSheet open={open} onClose={onClose} head={<SheetHead title="화면 모드" />}>
      <View style={styles.list}>
        {OPTIONS.map((o) => (
          <Pressable key={o.k} onPress={() => pick(o.k)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
            <Text style={styles.label}>{o.label}</Text>
            <CheckCircle on={pref === o.k} />
          </Pressable>
        ))}
      </View>
    </BottomSheet>
  );
}

const useStyles = createStyles((colors) => ({
  list: { paddingTop: 8 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 14 },
  label: { ...type.listLabel, color: colors.text },
}));
