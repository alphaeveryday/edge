import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useStories = () => useQuery({ queryKey: ['story', 'queue'], queryFn: () => api.story.queue() });
