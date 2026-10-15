import { Image, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';
import { NOTE } from './copy';

// AI 분석 본문 끝에 항상 두는 브랜드와 면책 문구
export function DisclaimerNote({ style }: { style?: StyleProp<ViewStyle> }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={style}>
      <View style={styles.brand}>
        <Image source={require('../../../assets/logo-mark.png')} style={[styles.logo, { tintColor: colors.textMuted }]} />
        <Text style={styles.name}>ETF Orca</Text>
      </View>
      <Text style={styles.note}>{NOTE}</Text>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  brand: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  logo: { width: 16, height: 16 },
  name: { fontFamily: fam.bold, fontSize: 12, color: colors.textMuted },
  note: { fontFamily: fam.regular, fontSize: 12, lineHeight: 18, color: colors.textFaint, marginTop: 6 },
}));
