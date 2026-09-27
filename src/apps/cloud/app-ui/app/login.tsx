import { useRouter } from 'expo-router';
import { Pressable, Text } from 'react-native';
import { Placeholder } from '@/components/Placeholder';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';

export default function Login() {
  const router = useRouter();
  const { login, finishOnboarding } = useSession();
  const done = () => {
    login();
    finishOnboarding();
    router.replace('/(tabs)/home');
  };
  return (
    <>
      <Placeholder title="로그인하면 관심 종목이 저장돼요" />
      <Pressable onPress={done} style={{ padding: 20 }}>
        <Text style={{ color: colors.primary, fontSize: 15 }}>Apple 로 계속하기 (mock)</Text>
      </Pressable>
    </>
  );
}
