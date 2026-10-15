import { Image, Text, View } from 'react-native';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';

// 로그인·회원가입 상단의 로고 마크와 워드마크
export function AuthBrand() {
  const styles = useStyles();
  return (
    <View style={styles.row}>
      <Image source={require('../../../assets/logo-mark.png')} style={styles.logo} />
      <Text style={styles.name}>ETF Orca</Text>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', alignSelf: 'center', gap: 8, marginTop: 48 },
  logo: { width: 36, height: 36, tintColor: colors.text },
  name: { fontFamily: fam.extrabold, fontSize: 32, letterSpacing: -1, color: colors.text },
}));
