import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { CtaButton, NavBar, SectorIcon } from '@/components/ui';
import { DeleteGroupSheet } from '@/features/watch/DeleteGroupSheet';
import { GroupChips } from '@/features/watch/GroupChips';
import { GroupEditSheet } from '@/features/watch/GroupEditSheet';
import { NewGroupSheet } from '@/features/watch/NewGroupSheet';
import { useSetMembers, useWatchGroups, useWatchList } from '@/features/watch/queries';
import { useWatchGroup } from '@/store/watch';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function WatchEdit() {
  const router = useRouter();
  const { top, bottom } = useSafeAreaInsets();
  const group = useWatchGroup((s) => s.group);
  const { data: groups } = useWatchGroups();
  const { data } = useWatchList(group);
  const save = useSetMembers();
  const [newOpen, setNewOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const g = groups?.find((x) => x.key === group);
  const remove = (code: string) => save.mutate({ group, codes: (data ?? []).filter((e) => e.code !== code).map((e) => e.code) });
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="관심 편집" onBack={() => router.back()} rightLabel={group === 'base' ? '' : '그룹 삭제'} rightColor={colors.up} onRight={() => setDelOpen(true)} />
      <View style={styles.chips}>
        <GroupChips onAdd={() => setNewOpen(true)} />
      </View>
      <View style={styles.meta}>
        <Text style={styles.count}>{data?.length ?? 0}종</Text>
        <Text style={styles.hint}>손잡이를 끌어 순서를 바꿔요</Text>
      </View>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingHorizontal: PAGE_X, paddingBottom: 20 }}>
        <Pressable onPress={() => setAddOpen(true)} style={({ pressed }) => [styles.addRow, pressed && { opacity: 0.6 }]}>
          <View style={styles.plus}>
            <Svg width={13} height={13} viewBox="0 0 14 14"><Path d="M7 2v10M2 7h10" stroke={colors.textSub} strokeWidth={2} strokeLinecap="round" /></Svg>
          </View>
          <Text style={styles.addText}>종목 추가하기</Text>
        </Pressable>
        {data?.map((e) => (
          <View key={e.code} style={styles.row}>
            <Pressable onPress={() => remove(e.code)} hitSlop={6} accessibilityLabel={`${e.name} 빼기`} style={styles.removeBtn}>
              <View style={styles.remove}><View style={styles.removeBar} /></View>
            </Pressable>
            <SectorIcon theme={e.theme} bg={e.logoBg} size={30} />
            <Text numberOfLines={1} style={styles.name}>{e.name}</Text>
            <View style={styles.grip}>
              {[0, 1, 2].map((i) => (
                <View key={i} style={styles.gripRow}><View style={styles.dot} /><View style={styles.dot} /></View>
              ))}
            </View>
          </View>
        ))}
      </ScrollView>
      <View style={[styles.foot, { paddingBottom: Math.max(bottom, 16) + 10 }]}>
        <CtaButton label="완료" tone="dark" onPress={() => router.back()} />
      </View>
      <NewGroupSheet open={newOpen} onClose={() => setNewOpen(false)} />
      <GroupEditSheet open={addOpen} group={group} groupLabel={g?.label ?? ''} onClose={() => setAddOpen(false)} />
      <DeleteGroupSheet group={delOpen && g ? g : null} onClose={() => setDelOpen(false)} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  chips: { paddingTop: 12, paddingHorizontal: PAGE_X },
  meta: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: 16, paddingBottom: 10, paddingHorizontal: PAGE_X },
  count: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  hint: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  addRow: { flexDirection: 'row', alignItems: 'center', gap: 9, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  plus: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  addText: { fontFamily: fam.bold, fontSize: 15, color: colors.textSub },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: colors.surface },
  removeBtn: { width: 26, height: 26, alignItems: 'center', justifyContent: 'center' },
  remove: { width: 19, height: 19, borderRadius: 999, backgroundColor: colors.up, alignItems: 'center', justifyContent: 'center' },
  removeBar: { width: 9, height: 2, borderRadius: 2, backgroundColor: colors.white },
  name: { flex: 1, fontFamily: fam.bold, fontSize: 15, color: colors.text },
  grip: { gap: 3, paddingVertical: 6, paddingHorizontal: 2 },
  gripRow: { flexDirection: 'row', gap: 3 },
  dot: { width: 3, height: 3, borderRadius: 999, backgroundColor: colors.lineStrong },
  foot: { paddingTop: 12, paddingHorizontal: PAGE_X, borderTopWidth: 1, borderTopColor: colors.surface },
});
