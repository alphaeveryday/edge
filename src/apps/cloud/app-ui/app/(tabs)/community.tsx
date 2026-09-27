import { Placeholder } from '@/components/Placeholder';

export default function Community() {
  return (
    <Placeholder
      title="커뮤니티"
      links={[
        { label: '게시물', href: '/post/1' },
        { label: '글쓰기', href: '/community/write' },
        { label: '내 프로필', href: '/profile/community' },
      ]}
    />
  );
}
