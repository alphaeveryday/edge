import { Text, View } from 'react-native';
import { chgColor, pct, won } from '@/lib/format';
import { createStyles, useColors } from '@/theme/theme';
import { fam, type } from '@/theme/typography';

export function RowQuote({ price, changePct }: { price: number; changePct: number }) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <View style={styles.row}>
      <Text style={styles.price}>{won(price)}</Text>
      <Text style={[styles.chg, { color: chgColor(colors, changePct) }]}>{pct(changePct)}</Text>
    </View>
  );
}

export function ChangeOnly({ changePct, size = 15 }: { changePct: number; size?: number }) {
  const styles = useStyles();
  const colors = useColors();
  return <Text style={[styles.only, { fontSize: size, color: chgColor(colors, changePct) }]}>{pct(changePct)}</Text>;
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  price: { ...type.mono, color: colors.textSub },
  chg: { fontFamily: fam.monoExtraBold, fontSize: 12.5 },
  only: { fontFamily: fam.monoExtraBold, letterSpacing: -0.3 },
}));
