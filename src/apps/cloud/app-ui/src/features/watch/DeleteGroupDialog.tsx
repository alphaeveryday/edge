import type { WatchGroup } from '@/api';
import { Dialog } from '@/components/ui';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';
import { useDeleteGroup } from './queries';

export function DeleteGroupDialog({ group, onClose }: { group: WatchGroup | null; onClose: () => void }) {
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
    <Dialog
      open={!!group}
      title={`${group?.label ?? ''} 그룹을 지울까요?`}
      sub={`그룹만 사라져요. 안에 있던 ${group?.count ?? 0}종은 기본 관심에 그대로 남아요.`}
      confirmLabel="지우기"
      danger
      busy={del.isPending}
      onConfirm={run}
      onClose={onClose}
    />
  );
}
