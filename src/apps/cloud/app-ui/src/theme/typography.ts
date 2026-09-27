import {
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
  JetBrainsMono_700Bold,
  JetBrainsMono_800ExtraBold,
} from '@expo-google-fonts/jetbrains-mono';

export const fontAssets = {
  'Pretendard-Regular': require('pretendard/dist/public/static/alternative/Pretendard-Regular.ttf'),
  'Pretendard-Medium': require('pretendard/dist/public/static/alternative/Pretendard-Medium.ttf'),
  'Pretendard-SemiBold': require('pretendard/dist/public/static/alternative/Pretendard-SemiBold.ttf'),
  'Pretendard-Bold': require('pretendard/dist/public/static/alternative/Pretendard-Bold.ttf'),
  'Pretendard-ExtraBold': require('pretendard/dist/public/static/alternative/Pretendard-ExtraBold.ttf'),
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
  JetBrainsMono_700Bold,
  JetBrainsMono_800ExtraBold,
};

// RN 은 커스텀 폰트에 fontWeight 를 적용하지 않으므로 굵기별 패밀리명을 직접 고른다
export const fam = {
  regular: 'Pretendard-Regular',
  medium: 'Pretendard-Medium',
  semibold: 'Pretendard-SemiBold',
  bold: 'Pretendard-Bold',
  extrabold: 'Pretendard-ExtraBold',
  mono: 'JetBrainsMono_400Regular',
  monoMedium: 'JetBrainsMono_500Medium',
  monoBold: 'JetBrainsMono_700Bold',
  monoExtraBold: 'JetBrainsMono_800ExtraBold',
} as const;
