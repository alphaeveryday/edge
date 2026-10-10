import { Text, View } from 'react-native';
import { Icon } from '@/components/ui/Icon';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

// 아직 발행·수집되지 않은 내용의 자리
export function Pending({ title = '준비 중이에요', sub = '자료가 확인되면 여기에 올라와요' }: { title?: string; sub?: string }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={styles.root}>
      <View style={styles.icon}>
        <Icon name="clock" color={colors.textFaint} size={26} />
      </View>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.sub}>{sub}</Text>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { alignItems: 'center', paddingVertical: 56, paddingHorizontal: 32, gap: 8 },
  icon: { width: 56, height: 56, borderRadius: 999, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center', marginBottom: 6 },
  title: { fontFamily: fam.bold, fontSize: 16, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 14, lineHeight: 21, color: colors.textMuted, textAlign: 'center' },
}));
