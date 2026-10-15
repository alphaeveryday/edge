import { useEffect, useRef } from 'react';
import { Animated, View } from 'react-native';
import { createStyles } from '@/theme/theme';

// 목록과 본문 공용 스켈레톤
export function Loading({ rows = 4 }: { rows?: number }) {
  const styles = useStyles();
  const op = useRef(new Animated.Value(0.5)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(op, { toValue: 1, duration: 700, useNativeDriver: true }),
      Animated.timing(op, { toValue: 0.5, duration: 700, useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [op]);
  return (
    <Animated.View style={[styles.root, { opacity: op }]}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={styles.row}>
          <View style={styles.circle} />
          <View style={{ flex: 1, gap: 8 }}>
            <View style={[styles.bar, { width: i % 2 ? '55%' : '75%' }]} />
            <View style={[styles.bar, { width: '35%' }]} />
          </View>
        </View>
      ))}
    </Animated.View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { paddingTop: 20, paddingHorizontal: 20, gap: 22 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  circle: { width: 36, height: 36, borderRadius: 999, backgroundColor: colors.surface },
  bar: { height: 12, borderRadius: 6, backgroundColor: colors.surface },
}));
