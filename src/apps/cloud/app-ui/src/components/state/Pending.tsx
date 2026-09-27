import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 아직 발행·수집되지 않은 내용의 자리
export function Pending({ title = '준비 중이에요', sub = '자료가 확인되면 여기에 올라와요' }: { title?: string; sub?: string }) {
  return (
    <View style={styles.root}>
      <View style={styles.icon}>
        <Svg width={26} height={26} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={9} stroke={colors.textFaint} strokeWidth={1.8} fill="none" />
          <Path d="M12 7v5l3 2" stroke={colors.textFaint} strokeWidth={1.8} fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </Svg>
      </View>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.sub}>{sub}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { alignItems: 'center', paddingVertical: 56, paddingHorizontal: 32, gap: 8 },
  icon: { width: 56, height: 56, borderRadius: 999, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center', marginBottom: 6 },
  title: { fontFamily: fam.bold, fontSize: 16, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 14, lineHeight: 21, color: colors.textMuted, textAlign: 'center' },
});
