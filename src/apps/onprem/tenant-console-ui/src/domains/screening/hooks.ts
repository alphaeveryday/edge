/* screening 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { screeningRepository } from './index';
import type { AutoPublishCriteria, NewBannedWord } from './types';

const WORDS_KEY = ['screening', 'words'];
const CRITERIA_KEY = ['screening', 'criteria'];
const DISCLAIMER_KEY = ['screening', 'disclaimer'];
const VERSIONS_KEY = ['screening', 'versions'];

export function useBannedWords() {
  return useQuery({ queryKey: WORDS_KEY, queryFn: () => screeningRepository.listWords() });
}

export function useCriteria() {
  return useQuery({ queryKey: CRITERIA_KEY, queryFn: () => screeningRepository.getCriteria() });
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: WORDS_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY }); // 모든 변경 = 새 버전 발행
    },
  });
  const toggleWord = useMutation({
    mutationFn: (id: number) => screeningRepository.toggleWord(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: WORDS_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY }); // 모든 변경 = 새 버전 발행
    },
  });
  const updateCriteria = useMutation({
    mutationFn: (patch: Partial<AutoPublishCriteria>) => screeningRepository.updateCriteria(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CRITERIA_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY });
    },
  });
  const updateDisclaimer = useMutation({
    mutationFn: (text: string) => screeningRepository.updateDisclaimer(text),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DISCLAIMER_KEY });
      qc.invalidateQueries({ queryKey: VERSIONS_KEY });
    },
  });

  return { addWord, toggleWord, updateCriteria, updateDisclaimer };
}
