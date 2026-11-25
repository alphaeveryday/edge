# 화면

앱의 화면 34개를 그대로 옮겼다. 추정해서 다시 그린 것이 아니라, 앱 코드를 웹으로 빌드해 목 데이터로 띄운 뒤 각 화면의 결과물을 떠낸 것이다. 원본 화면과 겹쳐 비교했을 때 다른 픽셀은 화면당 최대 0.15%다.

- 기준: `alphaeveryday/edge`의 `dev@7f43ab1`, `apps/etforca/app-ui`, 목 데이터 모드, 402×874 화면.
- 라이트는 `Screen…`, 다크는 `ScreenDark…`라는 이름으로 컴포넌트 목록의 Screens, Screens Dark 묶음에 있다.
- 화면 안의 종목, 글, 숫자는 `src/api/mock`의 예시 데이터다. 실제 시세나 실제 사용자 글이 아니다.
- 화면은 떠낸 그대로라 색과 크기가 값으로 박혀 있다. 토큰을 바꿔도 이 화면들은 따라 바뀌지 않는다. 앱이 바뀌면 다시 떠야 한다.

## 화면 목록

굵게 적은 컴포넌트는 이 시스템에 안내가 있는 것이다.

| 화면 | 카드 | 라우트 | 진입 | 쓰는 공용 컴포넌트 |
| --- | --- | --- | --- | --- |
| 온보딩 1 · 뉴스 | `ScreenOnboardingIntro1` | `/onboarding/intro` | 첫 실행의 첫 장 | BottomBar, **CtaButton**, **Sticker** |
| 온보딩 2 · 스티커 | `ScreenOnboardingIntro2` | `/onboarding/intro` | 같은 라우트의 둘째 장 | BottomBar, **CtaButton**, **Sticker** |
| 온보딩 · ETF 선택 | `ScreenOnboardingEtf` | `/onboarding/etf` | 고르면 홈으로 간다 | BottomBar, **CtaButton**, NavBar, PageTitle, **SearchField**, SectorIcon |
| 홈 | `ScreenHome` | `/(tabs)/home` | 탭 1 | Avatar, **Chip**, LinkRow, PageTitle, PostActions, **SectionHead**, SectorIcon, **Sticker**, TabBarLine |
| 관심 | `ScreenWatch` | `/(tabs)/watch` | 탭 2 | BottomSheet, **Chip**, **CtaButton**, PageTitle, SectorIcon, SheetHead, **Sticker**, TabBarLine |
| 관심 편집 | `ScreenWatchEdit` | `/watch/edit` |   | BottomBar, BottomSheet, **Chip**, **CtaButton**, Dialog, NavBar, SectorIcon, SheetHead |
| 종목 추가 | `ScreenWatchAdd` | `/watch/add` | 온보딩 ETF 그리드 재사용 | BottomBar, **CtaButton**, NavBar, PageTitle, **SearchField**, SectorIcon |
| 그룹 시트 | `ScreenSheetGroup` | `/(tabs)/watch` | 관심에서 + 그룹 | BottomSheet, **Chip**, **CtaButton**, PageTitle, SectorIcon, SheetHead, **Sticker**, TabBarLine |
| 탐색 | `ScreenExplore` | `/(tabs)/explore` | 탭 3 | BottomSheet, **CtaButton**, IconButton, LinkRow, PageTitle, **SectionHead**, SectorIcon, SheetHead, **Sticker**, TabBarLine |
| 분석 상세 시트 | `ScreenSheetAnalysis` | `/(tabs)/explore` | 탐색 행을 누르면 열린다 | BottomSheet, **CtaButton**, IconButton, LinkRow, PageTitle, **SectionHead**, SectorIcon, SheetHead, **Sticker**, TabBarLine |
| 투자 유의 사항 시트 | `ScreenSheetDisclaimer` | `/(tabs)/explore` | 구역 제목의 정보 아이콘 | BottomSheet, **CtaButton**, IconButton, LinkRow, PageTitle, **SectionHead**, SectorIcon, SheetHead, **Sticker**, TabBarLine |
| 커뮤니티 | `ScreenCommunity` | `/(tabs)/community` | 탭 4 | Avatar, **Chip**, IconButton, PageTitle, PostActions, SectorIcon, TabBarLine |
| 게시물 | `ScreenPost` | `/post/[id]` |   | Avatar, BottomBar, BottomSheet, Dialog, **ListRow**, NavBar, PostActions, SectorIcon, SheetHead |
| 글쓰기 | `ScreenCommunityWrite` | `/community/write` | 회원 | Avatar, BottomBar, BottomSheet, **CtaButton**, IconButton, SectorIcon, SheetHead |
| 검색 | `ScreenSearch` | `/search` | 최근 본 ETF | NavBar, **SearchField**, **SectionHead**, SectorIcon, **Sticker** |
| 검색 결과 | `ScreenSearchResults` | `/search` |   | NavBar, **SearchField**, **SectionHead**, SectorIcon, **Sticker** |
| 검색 결과 없음 | `ScreenSearchEmpty` | `/search` |   | NavBar, **SearchField**, **SectionHead**, SectorIcon, **Sticker** |
| ETF 상세 · AI 분석 | `ScreenEtfBrief` | `/etf/[code]/brief` |   | BottomSheet, **CtaButton**, IconButton, LinkRow, NavBar, SectorIcon, SheetHead, **Sticker**, **TabItem** |
| ETF 상세 · 오늘 움직임 | `ScreenEtfSummary` | `/etf/[code]/summary` |   | BottomSheet, **CtaButton**, LinkRow, NavBar, SectorIcon, SheetHead, **Sticker**, **TabItem** |
| 오늘 움직임 상세 시트 | `ScreenSheetMove` | `/etf/[code]/summary` | 자세히 보기 | BottomSheet, **CtaButton**, LinkRow, NavBar, SectorIcon, SheetHead, **Sticker**, **TabItem** |
| ETF 상세 · 커뮤니티 | `ScreenEtfCommunity` | `/etf/[code]/community` |   | Avatar, BottomSheet, **CtaButton**, IconButton, NavBar, PostActions, SectorIcon, SheetHead, **Sticker**, **TabItem** |
| ETF 상세 · 종목정보 | `ScreenEtfData` | `/etf/[code]/data` |   | BottomSheet, **Chip**, **CtaButton**, LinkRow, NavBar, **SectionHead**, SectorIcon, SheetHead, **Sticker**, **TabItem** |
| 요인 상세 | `ScreenFactor` | `/factor/[code]/[axis]` | AI 분석 탭에서 진입 | IconButton, **Sticker** |
| 요인 지표 | `ScreenMetric` | `/metric/[code]/[axis]` | AI 분석 탭의 축 칩 | IconButton, **Sticker** |
| 알림 | `ScreenNotifications` | `/notifications` |   | Avatar, NavBar, **TabItem** |
| 전체 메뉴 · 비회원 | `ScreenMenu` | `/menu` |   | Avatar, BottomSheet, IconButton, **ListRow**, SheetHead, ToggleRow |
| 전체 메뉴 · 회원 | `ScreenMenuMember` | `/menu` |   | Avatar, BottomSheet, IconButton, **ListRow**, SheetHead, ToggleRow |
| 화면 모드 시트 | `ScreenSheetTheme` | `/menu` | 전체 메뉴에서 화면 모드 | Avatar, BottomSheet, IconButton, **ListRow**, SheetHead, ToggleRow |
| 로그인 | `ScreenLogin` | `/login` | 모달 | **CtaButton**, NavBar |
| 회원가입 | `ScreenSignup` | `/auth/signup` | 모달 | BottomBar, **CtaButton**, NavBar |
| 비밀번호 재설정 | `ScreenReset` | `/auth/reset` | 모달 | **CtaButton**, NavBar, PageTitle |
| 닉네임 설정 | `ScreenNick` | `/auth/nick` | 소셜 가입 직후 | **CtaButton** |
| 계정 | `ScreenProfile` | `/profile` | 회원 | Avatar, Dialog, **ListRow**, NavBar, ToggleRow |
| 내 프로필 | `ScreenProfileCommunity` | `/profile/community` | 회원 | Avatar, NavBar, PostActions, **SectionHead**, SectorIcon |

## 흐름

- 첫 실행은 온보딩 두 장과 ETF 선택을 거쳐 홈으로 간다. 가입 화면은 첫 진입에 없다.
- 탭은 홈, 관심, 탐색, 커뮤니티 네 개다. 검색, 알림, 전체 메뉴는 오른쪽 위 아이콘에서 연다.
- 홈의 관심 줄은 ETF 상세의 오늘 움직임 탭으로, 관심 탭의 줄은 AI 분석 탭으로 간다.
- 탐색의 행을 누르면 분석 상세 시트가 열리고, 시트에서 ETF 상세로 갈 수 있다.
- ETF 상세는 AI 분석, 오늘 움직임, 커뮤니티, 종목정보 네 탭이다. AI 분석 탭에서 요인 상세와 요인 지표로 들어간다.
- 조회, 관심, 온보딩은 로그인 없이 된다. 투표, 글쓰기, 답글, 좋아요는 로그인 화면으로 보낸다.

## 화면 구성에서 읽히는 규칙

- **위쪽 구조는 둘 중 하나다.** 탭 화면은 큰 화면 제목(PageTitle)과 오른쪽 위 아이콘 세 개, 들어간 화면은 가운데 제목의 내비게이션 바(NavBar)다.
- **주요 행동은 화면 맨 아래 하나다.** 온보딩, 종목 추가, 관심 편집, 가입이 모두 아래에 고정된 CtaButton 하나를 쓴다.
- **전망은 어디서나 같은 스티커로 나온다.** 홈, 관심, 탐색, 검색, ETF 상세, 시트에서 모양과 다섯 단계 이름이 같다.
- **덧붙는 내용은 시트로 올린다.** 분석 상세, 오늘 움직임 상세, 그룹, 화면 모드, 유의 사항이 모두 아래에서 올라오는 BottomSheet다.
- **가장 많이 쓰이는 공용 컴포넌트**는 라우트 25개 기준으로 CtaButton, NavBar, SectorIcon(각 15개), Sticker(11), BottomSheet와 SheetHead(각 10), Avatar(9)다.

## 옮기지 않은 상태

- 로딩, 오류, 준비 중 화면과 빈 관심 목록
- 힌트 시트, 그룹 삭제 확인, 출처 목록, 글 삭제 확인, 신고
- 이용약관과 개인정보 처리방침 본문
- 눌렀을 때, 넘길 때의 움직임. 화면은 멈춘 한 장면이다.
- 기기의 상태 표시줄과 홈 표시줄. 웹 빌드에는 없다.
