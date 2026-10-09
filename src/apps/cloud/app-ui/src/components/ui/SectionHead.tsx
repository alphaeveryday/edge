import { Pressable, Text, View } from 'react-native';
import { Chevron } from './Chevron';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

export function SectionHead({ title, actionLabel, onAction, meta }: { title: string; actionLabel?: string; onAction?: () => void; meta?: string }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={styles.root}>
      <Text style={styles.title}>{title}</Text>
      {actionLabel ? (
        <Pressable onPress={onAction} style={styles.action}>
          <Text style={styles.actionText}>{actionLabel}</Text>
          <Chevron size={14} color={colors.primary} />
        </Pressable>
      ) : meta ? (
        <Text style={styles.meta}>{meta}</Text>
      ) : null}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flexDirection: 'row', alignItems: 'baseline', gap: 8, paddingHorizontal: PAGE_X },
  title: { flex: 1, ...type.sectionTitle, color: colors.text },
  action: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  actionText: { fontFamily: fam.bold, fontSize: 12.5, color: colors.primary },
  meta: { ...type.caption, color: colors.textMuted },
}));
