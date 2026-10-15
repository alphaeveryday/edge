/* screening 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { screeningRepository } from './index';
import type { AutoPublishCriteria, NewBannedWord } from './types';

const WORDS_KEY = ['screening', 'words'];
const POLICY_KEY = ['screening', 'policy'];
const DISCLAIMER_KEY = ['screening', 'disclaimer'];
const VERSIONS_KEY = ['screening', 'versions'];

export function useBannedWords() {
  return useQuery({ queryKey: WORDS_KEY, queryFn: () => screeningRepository.listWords() });
}

export function useActivePolicy() {
  return useQuery({ queryKey: POLICY_KEY, queryFn: () => screeningRepository.getActivePolicy() });
}

export function useDisclaimer() {
  return useQuery({ queryKey: DISCLAIMER_KEY, queryFn: () => screeningRepository.getDisclaimer() });
}

export function usePolicyVersions() {
  return useQuery({ queryKey: VERSIONS_KEY, queryFn: () => screeningRepository.listVersions() });
}

export function useScreeningActions() {
  const qc = useQueryClient();

  const addWord = useMutation({
    mutationFn: (word: NewBannedWord) => screeningRepository.addWord(word),
    // 실패(경합 409·stale id 404)도 원인이 낡은 캐시라 settle 시 무효화로 수렴한다.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: WORDS_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY }); // 모든 변경 = 새 버전 발행
      // 발행은 룰을 새 버전으로 복사한다 — id 가 바뀌므로 정책 캐시도 함께 버린다.
      qc.invalidateQueries({ queryKey: POLICY_KEY });
    },
  });
  const toggleWord = useMutation({
    mutationFn: (id: number) => screeningRepository.toggleWord(id),
    // 실패(경합 409·stale id 404)도 원인이 낡은 캐시라 settle 시 무효화로 수렴한다.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: WORDS_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY }); // 모든 변경 = 새 버전 발행
      // 발행은 룰을 새 버전으로 복사한다 — id 가 바뀌므로 정책 캐시도 함께 버린다.
      qc.invalidateQueries({ queryKey: POLICY_KEY });
    },
  });
  const updateCriteria = useMutation({
    mutationFn: (patch: Partial<AutoPublishCriteria>) => screeningRepository.updateCriteria(patch),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: POLICY_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY });
      // 기준 변경도 발행이라 룰 전체가 새 버전으로 복사된다 — 금칙어 id 까지 바뀌므로
      // 금칙어 캐시도 함께 버린다. 안 버리면 낡은 id 로 토글해 404(CNSL4042)가 난다.
      qc.invalidateQueries({ queryKey: WORDS_KEY });
    },
  });
  const updateDisclaimer = useMutation({
    mutationFn: (text: string) => screeningRepository.updateDisclaimer(text),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: DISCLAIMER_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY });
      // 문구 변경도 발행이라 룰 전체가 새 버전으로 복사된다 — 금칙어 id 까지 바뀌므로
      // 정책·금칙어 캐시를 함께 버린다. 안 버리면 낡은 id 로 토글해 404(CNSL4042)가 난다.
      qc.invalidateQueries({ queryKey: POLICY_KEY });
      qc.invalidateQueries({ queryKey: WORDS_KEY });
    },
  });

  return { addWord, toggleWord, updateCriteria, updateDisclaimer };
}
