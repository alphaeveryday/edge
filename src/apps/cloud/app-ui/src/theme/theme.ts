import { Appearance, Platform, StyleSheet, useColorScheme } from 'react-native';
import { create } from 'zustand';
import { themePref } from '@/api/http/storage';
import { makeSignal, palettes, type Palette, type Scheme } from './tokens';

export type ThemePref = 'system' | Scheme;
const isPref = (v: string | null): v is ThemePref => v === 'system' || v === 'light' || v === 'dark';

// 키보드·알림창 같은 네이티브 요소의 모드 맞춤
const applyNative = (p: ThemePref) => {
  if (Platform.OS !== 'web') Appearance.setColorScheme(p === 'system' ? 'unspecified' : p);
};

interface ThemeState {
  pref: ThemePref;
  restored: boolean;
  setPref: (p: ThemePref) => void;
  restore: () => Promise<void>;
}

export const useThemePref = create<ThemeState>((set) => ({
  pref: 'system',
  restored: false,
  setPref: (pref) => {
    set({ pref });
    applyNative(pref);
    themePref.save(pref);
  },
  restore: async () => {
    const v = await themePref.load();
    const pref = isPref(v) ? v : 'system';
    applyNative(pref);
    set({ pref, restored: true });
  },
}));

export function useScheme(): Scheme {
  const sys = useColorScheme();
  const pref = useThemePref((s) => s.pref);
  if (pref !== 'system') return pref;
  return sys === 'dark' ? 'dark' : 'light';
}

export const useColors = (): Palette => palettes[useScheme()];

const signals = { light: makeSignal(palettes.light), dark: makeSignal(palettes.dark) };
export const useSignal = () => signals[useScheme()];

// 모드별 한 번 만든 스타일의 재사용
export function createStyles<T extends StyleSheet.NamedStyles<T>>(fn: (c: Palette) => T) {
  const cache: Partial<Record<Scheme, T>> = {};
  return (): T => {
    const s = useScheme();
    return (cache[s] ??= StyleSheet.create(fn(palettes[s])));
  };
}
