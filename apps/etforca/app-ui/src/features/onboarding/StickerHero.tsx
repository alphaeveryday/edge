import { Text, View } from 'react-native';
import Svg, { Circle, Defs, RadialGradient, Stop } from 'react-native-svg';
import { Icon } from '@/components/ui/Icon';
import { Sticker } from '@/components/ui';
import { createStyles, useColors } from '@/theme/theme';
import { SIGNAL_ORDER } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const HINT: Record<string, string> = { strongUp: '지금', neutral: '지켜봐요', strongDown: '피해요' };
const GLOW = 380;

export function StickerHero() {
  const styles = useStyles();
  const colors = useColors();
  const AXES = [
    { name: '이슈', q: '사건이 진짜인가', c: colors.up },
    { name: '차트', q: '주가가 따라왔나', c: colors.down },
    { name: '매크로', q: '밖에서 방해하나', c: colors.warn },
    { name: '밸류', q: '이익 대비 싼가', c: '#8B34E0' },
    { name: '수급', q: '큰손이 믿나', c: colors.positive },
  ];
  
  return (
    <>
      <Svg width={GLOW} height={GLOW} style={styles.glow}>
        <Defs>
          <RadialGradient id="stickerGlow" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={colors.primarySoft} stopOpacity={1} />
            <Stop offset="0.55" stopColor={colors.primarySoft} stopOpacity={0.6} />
            <Stop offset="1" stopColor={colors.primarySoft} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Circle cx={GLOW / 2} cy={GLOW / 2} r={GLOW / 2} fill="url(#stickerGlow)" />
      </Svg>
      <View style={styles.stack}>
        {[...SIGNAL_ORDER].reverse().map((s, i) => (
          <View key={s} style={[styles.row, { transform: [{ scale: i === 0 || i === 4 ? 1 : 0.92 }] }]}>
            <Text style={styles.hint}>{HINT[s] ?? ''}</Text>
            <Sticker signal={s} size={34} radius={11} />
            <View style={{ width: 44 }} />
          </View>
        ))}
      </View>
      <View style={styles.axesWrap}>
        <Icon name="arrow-up" color={colors.lineStrong} size={26} strokeWidth={2} />
        <View style={styles.axes}>
          {AXES.map((a) => (
            <View key={a.name} style={styles.axis}>
              <View style={[styles.axisDot, { backgroundColor: a.c }]} />
              <Text style={styles.axisName}>{a.name}</Text>
              <Text style={styles.axisQ}>{a.q}</Text>
            </View>
          ))}
        </View>
      </View>
    </>
  );
}

const useStyles = createStyles((colors) => ({
  glow: { position: 'absolute' },
  stack: { alignItems: 'center', gap: 9 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  hint: { width: 44, textAlign: 'right', fontFamily: fam.semibold, fontSize: 12, color: colors.textMuted },
  axesWrap: { alignItems: 'center', gap: 10, marginTop: 22 },
  axes: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 6, maxWidth: 330 },
  axis: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 7, paddingLeft: 9, paddingRight: 12 },
  axisDot: { width: 7, height: 7, borderRadius: 999 },
  axisName: { fontFamily: fam.bold, fontSize: 13, color: colors.text },
  axisQ: { fontFamily: fam.medium, fontSize: 13, color: colors.textMuted },
}));
