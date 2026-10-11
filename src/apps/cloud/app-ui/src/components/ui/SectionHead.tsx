import { Pressable, Text, View } from 'react-native';
import { Chevron } from './Chevron';
import { Icon } from './Icon';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X, radius } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

// badge 는 기준 시각 같은 회색 배지
export function SectionHead({ title, actionLabel, onAction, meta, badge, onInfo }: { title: string; actionLabel?: string; onAction?: () => void; meta?: string; badge?: string; onInfo?: () => void }) {
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
      ) : badge ? (
        <Text style={styles.badge}>{badge}</Text>
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
  badge: { alignSelf: 'center', fontFamily: fam.bold, fontSize: 11.5, color: colors.textMuted, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
}));
