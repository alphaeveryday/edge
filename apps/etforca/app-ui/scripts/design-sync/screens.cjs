// design-sync 가 찍는 화면 목록. 이름은 design/system 의 Screen… 카드와 맞춘다.
// state: guest(온보딩 끝, 비회원) | fresh(첫 실행) | member(mock 계정으로 로그인한 뒤 홈에서 steps 로 이동)
// steps: 화면을 연 뒤 할 일 — { click: '보이는 글자' } | { label: '아이콘 버튼 이름' } | { fill: ['placeholder', '값'] }
// member 는 주소로 바로 열 수 없다. mock 로그인은 메모리에만 있어 새로 고치거나 주소를 바꾸면 풀린다.
module.exports = [
  { group: '온보딩', name: 'OnboardingIntro1', title: '온보딩 1 · 뉴스', route: '/onboarding/intro', state: 'fresh' },
  { group: '온보딩', name: 'OnboardingEtf', title: '온보딩 · ETF 선택', route: '/onboarding/etf', state: 'fresh' },
  { group: '탭', name: 'Home', title: '홈', route: '/home' },
  { group: '탭', name: 'Watch', title: '관심', route: '/watch' },
  { group: '탭', name: 'Explore', title: '탐색', route: '/explore' },
  { group: '탭', name: 'Community', title: '커뮤니티', route: '/community' },
  { group: '관심', name: 'WatchEdit', title: '관심 편집', route: '/watch/edit' },
  { group: '관심', name: 'WatchAdd', title: '종목 추가', route: '/watch/add' },
  { group: 'ETF 상세', name: 'EtfBrief', title: 'ETF 상세 · AI 분석', route: '/etf/AXAI/brief' },
  { group: 'ETF 상세', name: 'EtfSummary', title: 'ETF 상세 · 오늘 움직임', route: '/etf/AXAI/summary' },
  { group: 'ETF 상세', name: 'EtfCommunity', title: 'ETF 상세 · 커뮤니티', route: '/etf/AXAI/community' },
  { group: 'ETF 상세', name: 'EtfData', title: 'ETF 상세 · 종목정보', route: '/etf/AXAI/data' },
  { group: 'ETF 상세', name: 'Factor', title: '요인 상세', route: '/factor/AXAI/이슈' },
  { group: 'ETF 상세', name: 'Metric', title: '요인 지표', route: '/metric/AXAI/이슈' },
  { group: '검색·알림', name: 'Search', title: '검색', route: '/search' },
  { group: '검색·알림', name: 'Notifications', title: '알림', route: '/notifications' },
  { group: '커뮤니티', name: 'Post', title: '게시물', route: '/post/p1' },
  { group: '커뮤니티', name: 'CommunityWrite', title: '글쓰기', route: '/community/write', state: 'member', steps: [{ click: '커뮤니티' }, { label: 'plus' }] },
  { group: '메뉴·계정', name: 'Menu', title: '전체 메뉴 · 비회원', route: '/menu' },
  { group: '메뉴·계정', name: 'SheetTheme', title: '화면 모드 시트', route: '/menu', steps: [{ click: '화면 모드' }] },
  { group: '메뉴·계정', name: 'MenuMember', title: '전체 메뉴 · 회원', route: '/menu', state: 'member', steps: [{ label: 'menu' }] },
  { group: '메뉴·계정', name: 'Profile', title: '계정', route: '/profile', state: 'member', steps: [{ label: 'menu' }, { click: '지수' }] },
  { group: '메뉴·계정', name: 'ProfileCommunity', title: '내 프로필', route: '/profile/community', state: 'member', steps: [{ label: 'menu' }, { click: '지수' }, { click: '지수' }] },
  { group: '로그인·가입', name: 'Login', title: '로그인', route: '/login' },
  { group: '로그인·가입', name: 'Signup', title: '회원가입', route: '/auth/signup' },
  { group: '로그인·가입', name: 'Reset', title: '비밀번호 재설정', route: '/auth/reset' },
  { group: '로그인·가입', name: 'Nick', title: '닉네임 설정', route: '/auth/nick' },
];
