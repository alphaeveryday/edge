import { useLocalSearchParams, useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { size } from '@/theme/tokens';

export default function Story() {
  const { etf } = useLocalSearchParams<{ etf: string }>();
  const router = useRouter();
  return (
    <View style={styles.root}>
      <Text style={styles.title}>스토리 · {etf}</Text>
      <Pressable onPress={() => router.back()}>
        <Text style={styles.close}>닫기</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#191F28', padding: 24, paddingTop: 72, gap: 16 },
  title: { color: '#FFFFFF', fontSize: size.h3, fontWeight: '800' },
  close: { color: '#FFFFFF', fontSize: size.base },
});
