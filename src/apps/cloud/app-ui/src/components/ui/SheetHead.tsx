import { StyleSheet, Text, View } from 'react-native';
import { IconButton } from './IconButton';
import { colors } from '@/theme/tokens';
import { type } from '@/theme/typography';

export function SheetHead({ title, sub, onClose }: { title: string; sub?: string; onClose?: () => void }) {
  return (
    <View style={styles.root}>
      <View style={styles.main}>
        <Text style={styles.title}>{title}</Text>
        {!!sub && <Text style={styles.sub}>{sub}</Text>}
      </View>
      {onClose && <IconButton icon="close" size={32} color={colors.textFaint} onPress={onClose} />}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  main: { flex: 1, gap: 8 },
  title: { ...type.sheetTitle, color: colors.text },
  sub: { ...type.body, color: colors.textMuted },
});
