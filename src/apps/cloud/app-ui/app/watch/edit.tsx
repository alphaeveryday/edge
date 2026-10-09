import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { BottomBar, CtaButton, NavBar } from '@/components/ui';
import { DeleteGroupDialog } from '@/features/watch/DeleteGroupDialog';
import { GroupChips } from '@/features/watch/GroupChips';
import { NewGroupSheet } from '@/features/watch/NewGroupSheet';
import { PickGroupSheet } from '@/features/watch/PickGroupSheet';
import { useSetMembers, useWatchGroups, useWatchList } from '@/features/watch/queries';
import { SortableRows } from '@/features/watch/SortableRows';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function WatchEdit() {
  const styles = useStyles();
  const colors = useColors();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const group = useWatchGroup((s) => s.group);
  const { data: groups } = useWatchGroups();
  const { data } = useWatchList(group);
  const save = useSetMembers();
  const [newOpen, setNewOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const g = groups?.find((x) => x.key === group);
  const toast = useToast((s) => s.show);
  const [sel, setSel] = useState<string[]>([]);
  const [moveOpen, setMoveOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  useEffect(() => setSel([]), [group]);
  const list = data ?? [];
  const picked = list.filter((e) => sel.includes(e.code));
  const allOn = list.length > 0 && picked.length === list.length;
  const toggle = (code: string) => setSel((p) => (p.includes(code) ? p.filter((x) => x !== code) : [...p, code]));
  const remove = () =>
    save.mutate({ group, codes: list.filter((e) => !sel.includes(e.code)).map((e) => e.code) }, {
      onSuccess: () => {
        toast(`${picked.length}개를 뺐어요`);
        setSel([]);
      },
    });
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="관심 편집" onBack={() => router.back()} rightLabel={group === 'base' ? '' : '그룹 삭제'} rightColor={colors.up} onRight={() => setDelOpen(true)} />
      <View style={styles.chips}>
        <GroupChips onAdd={() => setNewOpen(true)} />
      </View>
      <View style={styles.meta}>
        <Pressable onPress={() => setSel(allOn ? [] : list.map((e) => e.code))} hitSlop={6}>
          <Text style={styles.all}>{allOn ? '선택 해제' : '전체선택'}</Text>
        </Pressable>
        <Text style={styles.hint}>손잡이를 끌어 순서를 바꿔요</Text>
      </View>
      <ScrollView style={{ flex: 1 }} scrollEnabled={!dragging} contentContainerStyle={{ paddingHorizontal: PAGE_X, paddingBottom: 20 }}>
        <Pressable onPress={() => router.push('/watch/add')} style={({ pressed }) => [styles.addRow, pressed && { opacity: 0.6 }]}>
          <View style={styles.plus}>
            <Svg width={13} height={13} viewBox="0 0 14 14"><Path d="M7 2v10M2 7h10" stroke={colors.textSub} strokeWidth={2} strokeLinecap="round" /></Svg>
          </View>
          <Text style={styles.addText}>종목 추가하기</Text>
        </Pressable>
        <SortableRows etfs={list} selected={sel} onToggle={toggle} onReorder={(codes) => save.mutate({ group, codes })} onDragging={setDragging} />
      </ScrollView>
      <BottomBar style={styles.foot}>
        {picked.length ? (
          <View style={styles.actions}>
            <View style={{ flex: 1 }}><CtaButton label="삭제" tone="soft" onPress={remove} /></View>
            <View style={{ flex: 1.6 }}><CtaButton label={`${picked.length}개 그룹 이동`} onPress={() => setMoveOpen(true)} /></View>
          </View>
        ) : (
          <CtaButton label="완료" tone="dark" onPress={() => router.back()} />
        )}
      </BottomBar>
      <NewGroupSheet open={newOpen} onClose={() => setNewOpen(false)} />
      <PickGroupSheet etfs={moveOpen ? picked : []} title="어떤 그룹으로 옮길까요?" onClose={() => setMoveOpen(false)} />
      <DeleteGroupDialog group={delOpen && g ? g : null} onClose={() => setDelOpen(false)} />
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  chips: { paddingTop: 12, paddingHorizontal: PAGE_X },
  meta: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: 16, paddingBottom: 10, paddingHorizontal: PAGE_X },
  all: { fontFamily: fam.semibold, fontSize: 13, color: colors.textSub },
  hint: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  addRow: { flexDirection: 'row', alignItems: 'center', gap: 9, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  plus: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  addText: { fontFamily: fam.bold, fontSize: 15, color: colors.textSub },
  actions: { flexDirection: 'row', gap: 9 },
  foot: { paddingTop: 12, paddingHorizontal: PAGE_X, borderTopWidth: 1, borderTopColor: colors.surface },
}));
