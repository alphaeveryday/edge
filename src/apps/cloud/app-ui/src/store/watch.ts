import { create } from 'zustand';

// 홈·관심 탭이 공유하는 현재 관심 그룹
interface WatchState {
  group: string;
  setGroup: (k: string) => void;
}

export const useWatchGroup = create<WatchState>((set) => ({
  group: 'base',
  setGroup: (group) => set({ group }),
}));
