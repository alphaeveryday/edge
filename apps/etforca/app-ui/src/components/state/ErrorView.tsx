import { Text, View } from 'react-native';
import { CtaButton } from '@/components/ui';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';

export function ErrorView({ onRetry, title = '불러오지 못했어요', sub = '연결을 확인하고 다시 시도해 주세요' }: { onRetry?: () => void; title?: string; sub?: string }) {
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Text style={styles.mark}>!</Text>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.sub}>{sub}</Text>
      {onRetry && (
        <View style={{ marginTop: 10, width: 140 }}>
          <CtaButton label="다시 시도" tone="soft" size="sm" onPress={onRetry} />
        </View>
      )}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { alignItems: 'center', paddingVertical: 56, paddingHorizontal: 32, gap: 8 },
  mark: { width: 56, height: 56, lineHeight: 56, borderRadius: 999, backgroundColor: colors.surface, textAlign: 'center', fontFamily: fam.extrabold, fontSize: 22, color: colors.textSub, overflow: 'hidden', marginBottom: 6 },
  title: { fontFamily: fam.bold, fontSize: 16, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 14, lineHeight: 21, color: colors.textMuted, textAlign: 'center' },
}));
