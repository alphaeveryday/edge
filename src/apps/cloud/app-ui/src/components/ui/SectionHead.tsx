import { Pressable, Text, View } from 'react-native';
import { Chevron } from './Chevron';
import { Icon } from './Icon';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

export function SectionHead({ title, actionLabel, onAction, meta, onInfo }: { title: string; actionLabel?: string; onAction?: () => void; meta?: string; onInfo?: () => void }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={styles.root}>
      <View style={styles.head}>
        <Text style={styles.title}>{title}</Text>
        {onInfo && (
          <Pressable onPress={onInfo} hitSlop={10} accessibilityLabel="투자 유의 사항">
            <Icon name="info" color={colors.textFaint} size={18} />
          </Pressable>
        )}
      </View>
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
  head: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
  title: { ...type.sectionTitle, color: colors.text },
  action: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  actionText: { fontFamily: fam.bold, fontSize: 12.5, color: colors.primary },
  meta: { ...type.caption, color: colors.textMuted },
}));
