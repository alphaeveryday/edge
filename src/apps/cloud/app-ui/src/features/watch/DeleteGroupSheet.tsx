import { StyleSheet, View } from 'react-native';
import type { WatchGroup } from '@/api';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';
import { useDeleteGroup } from './queries';

export function DeleteGroupSheet({ group, onClose }: { group: WatchGroup | null; onClose: () => void }) {
  const del = useDeleteGroup();
  const setGroup = useWatchGroup((s) => s.setGroup);
  const toast = useToast((s) => s.show);
  const run = () =>
    group &&
    del.mutate(group.key, {
      onSuccess: () => {
        setGroup('base');
        onClose();
        toast('그룹을 지웠어요');
      },
    });
  return (
    <BottomSheet open={!!group} onClose={onClose}>
      <SheetHead title={`${group?.label ?? ''} 그룹을 지울까요?`} sub={`그룹만 사라져요. 안에 있던 ${group?.count ?? 0}종은 기본 관심에 그대로 남아요.`} />
      <View style={styles.btns}>
        <CtaButton label="취소" tone="soft" grow onPress={onClose} />
        <CtaButton label="지우기" tone="danger" grow onPress={run} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({ btns: { flexDirection: 'row', gap: 8, marginTop: 20 } });
