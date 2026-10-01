import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Animated, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { TOP_BAR_H, TopBar } from '@/components/TopBar';
import { PageTitle } from '@/components/ui';
import { EtfRow } from '@/features/etf/EtfRow';
import { GroupChips } from '@/features/watch/GroupChips';
import { NewGroupSheet } from '@/features/watch/NewGroupSheet';
import { useGroupMembers, useWatchList } from '@/features/watch/queries';
import { useSwapFade } from '@/lib/useSwapFade';
import { useWatchGroup } from '@/store/watch';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Watch() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const group = useWatchGroup((s) => s.group);
  const list = useWatchList(group, true);
  const data = list.data;
  // 칩 전환의 즉시 표시용 모든 그룹 미리 받기
  useGroupMembers();
  const fade = useSwapFade(group, list.isPlaceholderData);
  const [newOpen, setNewOpen] = useState(false);
  return (
    <View style={styles.root}>
      <TopBar />
      <ScrollView contentContainerStyle={{ paddingTop: top + TOP_BAR_H, paddingBottom: 36 }} showsVerticalScrollIndicator={false}>
        <PageTitle title="관심" />
        <View style={styles.chips}>
          <GroupChips onAdd={() => setNewOpen(true)} onEdit={() => router.push('/watch/edit')} />
        </View>
        <Animated.View style={{ opacity: fade }}>
          <View style={{ paddingHorizontal: PAGE_X }}>
            {data?.map((e) => <EtfRow key={e.code} etf={e} />)}
          </View>
          {data && data.length === 0 && <Text style={styles.empty}>이 그룹에 담긴 ETF가 없어요{'\n'}편집에서 종목을 추가해 보세요</Text>}
        </Animated.View>
      </ScrollView>
      <NewGroupSheet open={newOpen} onClose={() => setNewOpen(false)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  chips: { paddingVertical: 12, paddingHorizontal: PAGE_X, borderBottomWidth: 1, borderBottomColor: colors.surface },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textSub, paddingVertical: 34, paddingHorizontal: 20 },
});
