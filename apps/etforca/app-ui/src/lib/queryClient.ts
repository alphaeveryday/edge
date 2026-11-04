import { MutationCache, QueryClient } from '@tanstack/react-query';
import { tokens } from '@/api/http/storage';

// HTTP 응답 뒤의 매핑 작업까지 끝난 이전 세션의 쓰기도 캐시에 반영하지 않는다.
const versions = new WeakMap<object, number>();
export const queryClient = new QueryClient({
  // 오프라인 쓰기를 보관했다가 다른 로그인 주체로 재전송하지 않는다.
  defaultOptions: { mutations: { networkMode: 'always' } },
  mutationCache: new MutationCache({
    onMutate: (_variables, mutation) => {
      const started = tokens.version();
      versions.set(mutation, started);
      const run = mutation.options.mutationFn;
      if (run) mutation.setOptions({ ...mutation.options, mutationFn: (...args) => {
        tokens.assertCurrent(started);
        return run(...args);
      } });
    },
    onSuccess: (_data, _variables, _context, mutation) => {
      const started = versions.get(mutation)!;
      // 인증 mutation 자신의 전환 1회만 허용한다. 그 뒤의 계정 변경은 이전 응답이다.
      const expected = mutation.options.meta?.sessionTransition && tokens.version() === started + 1 ? started + 1 : started;
      tokens.assertCurrent(expected);
      versions.set(mutation, expected);
    },
    // 훅의 비동기 onSuccess가 끝난 뒤 호출별 성공 콜백을 알리기 전에도 확인한다.
    onSettled: (_data, error, _variables, _context, mutation) => {
      if (!error) tokens.assertCurrent(versions.get(mutation)!);
    },
  }),
});
