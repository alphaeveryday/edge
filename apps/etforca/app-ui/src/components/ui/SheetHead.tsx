import { Text, View } from 'react-native';
import { createStyles } from '@/theme/theme';
import { type } from '@/theme/typography';

export function SheetHead({ title, sub }: { title: string; sub?: string }) {
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Text style={styles.title}>{title}</Text>
      {!!sub && <Text style={styles.sub}>{sub}</Text>}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { gap: 8 },
  title: { ...type.sheetTitle, color: colors.text },
  sub: { ...type.body, color: colors.textMuted },
}));
