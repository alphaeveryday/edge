import type { ReactNode } from 'react';
import { Text, View } from 'react-native';
import { createStyles } from '@/theme/theme';
import { PAGE_X, radius } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

// 오른쪽은 기준 시각 칩, 또는 화면별 내용
export function PageTitle({ title, sub, meta, right }: { title: string; sub?: string; meta?: string; right?: ReactNode }) {
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <View style={styles.main}>
        <Text style={styles.title}>{title}</Text>
        {!!sub && <Text style={styles.sub}>{sub}</Text>}
      </View>
      {!!meta && <Text style={styles.meta}>{meta}</Text>}
      {right && <View style={styles.right}>{right}</View>}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingTop: 12, paddingHorizontal: PAGE_X },
  main: { flex: 1, gap: 6 },
  title: { ...type.pageTitle, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, lineHeight: 18 },
  right: { alignSelf: 'center' },
  meta: { marginTop: 4, fontFamily: fam.bold, fontSize: 11.5, color: colors.textMuted, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
}));
