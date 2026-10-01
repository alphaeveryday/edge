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

// 커스텀 폰트 굵기 미적용 대응용 굵기별 패밀리
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

// 역할별 글꼴 스타일
export const type = {
  pageTitle: { fontFamily: fam.extrabold, fontSize: 24, letterSpacing: -0.7, lineHeight: 30 },
  sectionTitle: { fontFamily: fam.extrabold, fontSize: 20, letterSpacing: -0.6 },
  sheetTitle: { fontFamily: fam.extrabold, fontSize: 19, letterSpacing: -0.55, lineHeight: 25 },
  cardTitle: { fontFamily: fam.extrabold, fontSize: 17, letterSpacing: -0.5, lineHeight: 23 },
  navTitle: { fontFamily: fam.extrabold, fontSize: 16, letterSpacing: -0.3 },
  button: { fontFamily: fam.extrabold, fontSize: 15.5, letterSpacing: -0.15 },
  listLabel: { fontFamily: fam.semibold, fontSize: 15.5, letterSpacing: -0.3 },
  tab: { fontFamily: fam.semibold, fontSize: 14 },
  sticker: { fontFamily: fam.extrabold, fontSize: 12.5 },
  body: { fontFamily: fam.regular, fontSize: 14, lineHeight: 22 },
  caption: { fontFamily: fam.regular, fontSize: 12.5 },
  mono: { fontFamily: fam.monoBold, fontSize: 12.5, letterSpacing: -0.25 },
} as const;
