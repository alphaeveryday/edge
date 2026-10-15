import { NativeTabs } from 'expo-router/unstable-native-tabs';
import { Platform } from 'react-native';
import { useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

// iOS 는 리퀴드 글라스, Android 는 Material 하단 탭
export default function TabsLayout() {
  const colors = useColors();
  return (
    <NativeTabs
      minimizeBehavior="onScrollDown"
      tintColor={colors.text}
      iconColor={{ default: colors.textMuted, selected: colors.text }}
      labelStyle={{ default: { fontFamily: fam.semibold, color: colors.textMuted }, selected: { fontFamily: fam.semibold, color: colors.text } }}
      // iOS 는 배경색 지정 시 유리 효과가 사라져 Android 만 지정
      backgroundColor={Platform.OS === 'android' ? colors.bg : undefined}
      indicatorColor={colors.surface}
    >
      <NativeTabs.Trigger name="home">
        <NativeTabs.Trigger.Icon sf={{ default: 'house', selected: 'house.fill' }} md="home" />
        <NativeTabs.Trigger.Label>홈</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="watch">
        <NativeTabs.Trigger.Icon sf={{ default: 'heart', selected: 'heart.fill' }} md="favorite" />
        <NativeTabs.Trigger.Label>관심</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="explore">
        <NativeTabs.Trigger.Icon sf={{ default: 'safari', selected: 'safari.fill' }} md="explore" />
        <NativeTabs.Trigger.Label>탐색</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="community">
        <NativeTabs.Trigger.Icon sf={{ default: 'bubble.left', selected: 'bubble.left.fill' }} md="chat_bubble" />
        <NativeTabs.Trigger.Label>커뮤니티</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>
    </NativeTabs>
  );
}
