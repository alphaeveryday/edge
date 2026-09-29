import { useRouter } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useSession } from '@/store/session';

// 비로그인 상태의 투표·글쓰기·답글·좋아요에 뜨는 로그인 유도 시트
export function LoginGateSheet() {
  const router = useRouter();
  const { gateOpen, gateReason, closeGate } = useSession();
  const go = () => { closeGate(); router.push('/login'); };
  return (
    <BottomSheet open={gateOpen} onClose={closeGate}>
      <SheetHead title="로그인하면 이어서 할 수 있어요" sub={`${gateReason} 기능은 로그인 뒤에 열려요. 관심 종목과 알림도 함께 저장돼요.`} onClose={closeGate} />
      <View style={styles.btns}>
        <CtaButton label="로그인하기" tone="dark" onPress={go} />
        <CtaButton label="나중에 할게요" tone="soft" onPress={closeGate} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({ btns: { gap: 10, marginTop: 20 } });
