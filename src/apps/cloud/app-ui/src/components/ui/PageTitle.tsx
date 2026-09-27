import { StyleSheet, Text, View } from 'react-native';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export function PageTitle({ title, sub, meta }: { title: string; sub?: string; meta?: string }) {
  return (
    <View style={styles.root}>
      <View style={styles.main}>
        <Text style={styles.title}>{title}</Text>
        {!!sub && <Text style={styles.sub}>{sub}</Text>}
      </View>
      {!!meta && <Text style={styles.meta}>{meta}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingTop: 12, paddingHorizontal: PAGE_X },
  main: { flex: 1, gap: 6 },
  title: { fontFamily: fam.extrabold, fontSize: 24, color: colors.text, letterSpacing: -0.7, lineHeight: 30 },
  sub: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, lineHeight: 18 },
  meta: { marginTop: 4, fontFamily: fam.bold, fontSize: 11.5, color: colors.textMuted, backgroundColor: colors.surface, borderRadius: 8, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
});
