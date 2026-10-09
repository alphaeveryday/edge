import { StyleSheet, Text, View } from 'react-native';
import { colors } from '@/theme/tokens';
import { type } from '@/theme/typography';

export function SheetHead({ title, sub }: { title: string; sub?: string }) {
  return (
    <View style={styles.root}>
      <Text style={styles.title}>{title}</Text>
      {!!sub && <Text style={styles.sub}>{sub}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: 8 },
  title: { ...type.sheetTitle, color: colors.text },
  sub: { ...type.body, color: colors.textMuted },
});
