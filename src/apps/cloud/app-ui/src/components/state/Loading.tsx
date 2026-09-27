import { useEffect, useRef } from 'react';
import { Animated, StyleSheet, View } from 'react-native';
import { colors } from '@/theme/tokens';

// 목록·본문 공용 스켈레톤. rows 줄 수만 조절한다
export function Loading({ rows = 4 }: { rows?: number }) {
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

const styles = StyleSheet.create({
  root: { paddingTop: 20, paddingHorizontal: 20, gap: 22 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  circle: { width: 36, height: 36, borderRadius: 999, backgroundColor: colors.surface },
  bar: { height: 12, borderRadius: 6, backgroundColor: colors.surface },
});
