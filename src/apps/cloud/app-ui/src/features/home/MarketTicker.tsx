import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Text, View } from 'react-native';
import type { MarketIndex } from '@/api';
import { pct } from '@/lib/format';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

const EVERY = 3500;
const SHIFT = 14;

// 현재 시각 기준 순번, 탭 이동에도 같은 순번과 남은 시간
const slot = () => Math.floor(Date.now() / EVERY);

// 탭 제목 옆 시장 지수 한 줄 순환
// 움직임 줄이기 설정은 밀림 없이 교체
export function MarketTicker({ items }: { items: MarketIndex[] }) {
  const styles = useStyles();
  const colors = useColors();
  const [at, setAt] = useState(slot);
  const [still, setStill] = useState(false);
  const y = useRef(new Animated.Value(0)).current;
  const fade = useRef(new Animated.Value(1)).current;
  useEffect(() => { AccessibilityInfo.isReduceMotionEnabled().then(setStill, () => undefined); }, []);
  useEffect(() => {
    if (items.length < 2) return;
    let t: ReturnType<typeof setTimeout>;
    const step = () => setAt(slot());
    const next = () => {
      t = setTimeout(() => {
        next();
        if (still) return step();
        Animated.parallel([
          Animated.timing(y, { toValue: -SHIFT, duration: 180, useNativeDriver: true }),
          Animated.timing(fade, { toValue: 0, duration: 180, useNativeDriver: true }),
        ]).start(() => {
          step();
          y.setValue(SHIFT);
          Animated.parallel([
            Animated.timing(y, { toValue: 0, duration: 220, useNativeDriver: true }),
            Animated.timing(fade, { toValue: 1, duration: 220, useNativeDriver: true }),
          ]).start();
        });
      }, EVERY - (Date.now() % EVERY) + 20);
    };
    setAt(slot());
    next();
    return () => clearTimeout(t);
  }, [still, items.length]);
  const i = items[at % items.length];
  if (!i) return null;
  return (
    <View style={styles.clip}>
      <Animated.View style={[styles.row, { opacity: fade, transform: [{ translateY: y }] }]}>
        <Text style={styles.name}>{i.name}</Text>
        <Text style={styles.value}>{i.value.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</Text>
        <Text style={[styles.pct, { color: i.changePct > 0 ? colors.up : i.changePct < 0 ? colors.down : colors.textMuted }]}>{pct(i.changePct)}</Text>
      </Animated.View>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  clip: { alignSelf: 'flex-start', height: 20, overflow: 'hidden', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'baseline', gap: 7 },
  name: { fontFamily: fam.semibold, fontSize: 14, color: colors.textSub },
  value: { fontFamily: fam.monoMedium, fontSize: 14, color: colors.text },
  pct: { fontFamily: fam.monoMedium, fontSize: 14 },
}));
