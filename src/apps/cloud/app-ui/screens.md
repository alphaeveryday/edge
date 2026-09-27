# ETF Orca 화면 명세 (1단계)

출처: Claude Design 프로젝트 "스토리 뷰어 전망 변화 표시" → `MarketBrew App v188 copy 2.dc.html` (사본 `design/app-v188.dc.html`).
템플릿(마크업)은 완전하고, 스크립트(목 데이터·핸들러)는 256KiB 제한으로 `EXTRA_ETFS.NUKE` 중간에서 잘렸다. 잘린 뒤쪽 = 나머지 목 데이터 + 이벤트 핸들러.
pptx 32장(`tmp.md`)보다 이 파일이 최신이며 화면이 더 많다. 제품명은 ETF Orca (디자인 원문의 MarketBrew·EDGE 는 치환 대상).

## 화면 (풀스크린 26)

| 디자인 플래그 | 화면 | pptx 대응 | 비고 |
|---|---|---|---|
| obIsHow | 온보딩 1 · 뉴스 3만 건 | 온보딩 1 | |
| obIsSticker | 온보딩 2 · 전망 스티커 5단계 | 온보딩 2 | |
| obIsWhat | 온보딩 3 · 오늘 달라진 것 | 온보딩 3 | 카드 스택 애니메이션 |
| obIsTheme | 테마 선택 | 테마 선택 | 그룹별 칩(obThemeGroups) |
| obIsEtf | ETF 선택 | ETF 선택 | 검색·빈 상태 포함 |
| isSignup | 로그인 | 로그인 | Apple·Google·이메일 |
| isHome | 홈 | 홈 | 관심 그룹 탭(wGroups)·오늘 등락·빈 상태(watchEmpty) |
| isDiscoverDark | 관심 | 관심 | 빈 상태 포함 |
| isWatchEdit | 관심 편집 | 관심 편집 | 드래그 정렬·종목 추가 |
| isSearching | 검색 | 검색 | 최근 검색 칩·결과 없음 |
| isStock · isStockSummary | ETF 상세 · 요약(오늘 움직임) | ETF 상세·요약 | 차트 range 1D~MAX, "왜 움직였을까" |
| isStock · isStockBrief | ETF 상세 · AI 분석 | ETF 상세·AI 분석 | 단기 전망 스티커·요인 5행 |
| isStock · isStockData | ETF 상세 · 종목정보 | (없음, tmp.md 누락 목록) | 구성 히트맵·테마 비중·보유 종목 |
| isStock · isStockComm | ETF 상세 · 커뮤니티 | ETF 상세·커뮤니티 | 투표(pollSt)·글 목록 |
| isIssueList | 이슈 목록 | 이슈 | 관심 기준 빈 상태 |
| isLiveIssue | 실시간 이슈 상세 | 실시간 이슈 | 주목 포인트·출처(liSrcOpen)·연관 ETF |
| isCommunity | 커뮤니티 | 커뮤니티 | 투표(pollComm)·글쓰기 진입(commCanWrite) |
| isExplore | 탐색 | 탐색 | 대기(exIdle)·검색(exSearching)·테마 시트 |
| isExCmp | 탐색 · 테마 ETF 비교 | 테마 ETF | "전망 좋은 순", 데일리 준비 중 상태 |
| isThemes | 테마 목록 | 테마 | |
| isThemeDetail | 테마 분석 | 테마 분석 | 오늘 반영·무엇이 중요한가·우리의 전망·종목 시트 |
| isCommProfile | 커뮤니티 프로필 | 커뮤니티 프로필 | 빈 상태(cpEmpty) |
| isProfile | 계정 | 계정 | 알림 토글·로그아웃 |
| isNotis | 알림 | 알림 | 종류 필터·빈 상태 |
| storyOpen | 스토리 뷰어 | 스토리 | 진행 바·일시정지·전망 변화(stoChanged)·관련 이슈·차트 |
| postOpen | 게시물 상세 | 게시물 | 답글·인용 리포스트 |

## 시트·모달·오버레이 (19)

| 플래그 | 내용 | 뜨는 곳 |
|---|---|---|
| menuOpen | 전체 메뉴 (로그인/비로그인 상태) | 상단바 |
| factorPageOpen | 요인 상세 (단기 판단 기준) | AI 분석 |
| metricOpen | 요인 지표 (도움·중립·부담) | AI 분석 |
| daSheetOpen | 데일리 분석 시트 (5기준·출처) | AI 분석 |
| briefOpen | 한 줄 요약 → EDGE 판단 보기 | 요약 |
| sumEvtOpen | 요약 사건 상세 | 요약 |
| hintOpen | 힌트/설명 | 요약 |
| stockSheetOpen | 테마 내 종목 시트 | 테마 분석 |
| themeSheetOpen | 테마 ETF 시트 | 탐색 |
| pickOpen | 그룹 담기 | ETF 상세·검색 |
| newGroupOpen · groupEditOpen · delGroupOpen | 그룹 생성·편집·삭제 확인 | 관심 편집·그룹 담기 |
| moveOpen | 그룹 이동 | 관심 편집 |
| commWriteOpen | 글쓰기 | 커뮤니티·ETF 커뮤니티 |
| postMoreOpen | 게시물 더보기 (신고·삭제) | 게시물 |
| liSrcOpen | 출처 목록 | 실시간 이슈 |
| sumToastOn | 토스트 | 전역 |
| showTopBar · showTabs | 상단바·탭바 (관심·탐색·커뮤니티 3탭, 홈은 상단바) | 전역 |

제외 확정(2026-09-28): 투자 에이전트 채팅(`agentContext`, 미니플레이어), 인사이트 상세(`isInsight`, 홈 `feedMode` 노브는 미사용 잔재). 홈은 ETF 기반 레이아웃 하나로 간다.

## Expo Router 트리 (구현 기준, 2026-09-28)

```
app/
  _layout.tsx                # 루트 스택 + 폰트 로드 + Toast
  index.tsx                  # 온보딩 게이트
  onboarding/{how,sticker,what,theme,etf}.tsx
  login.tsx
  (tabs)/                    # 탭바: home · watch · explore · community
    home.tsx  watch/index.tsx  explore.tsx  community.tsx
  watch/edit.tsx             # 탭바 없는 전체 화면
  etf/[code]/_layout.tsx     # 헤더 + 4탭 세그먼트
  etf/[code]/{brief,summary,data,community}.tsx
  factor/[code]/[axis].tsx   # 요인 상세
  metric/[code]/[axis].tsx   # 요인 지표
  themes/index.tsx  themes/[id].tsx  themes/compare.tsx
  issues/index.tsx  issues/[id].tsx
  search.tsx  menu.tsx  notifications.tsx
  story/[etf].tsx            # 풀스크린 모달
  post/[id].tsx  community/write.tsx (모달)
  profile/index.tsx  profile/community.tsx
```

시트·모달은 컴포넌트(BottomSheet·HintSheet·DailySheet)로 두고, 스토리·글쓰기만 모달 라우트다. 요인 상세·지표·전체 메뉴는 디자인이 전체 화면 push 라 라우트로 뒀다.

## 상태·데이터 (디자인 state 기준)

- 사용자: 로그인 여부, 닉네임·핸들·아바타, 알림 설정, 테마
- 온보딩: step, 고른 테마, 고른 ETF
- 관심: `watch[]`, 그룹(wGroups), 정렬
- ETF: `ETFS`(AXAI·GRID·KBND·DEFN·MEDX·SOLR) + `EXTRA_ETFS`(SEMI·NUKE·…, 잘림) — 이름·가격·등락·스티커·요인 5행·히트맵·테마·보유종목·데일리 본문·일정
- 이슈: `ISSUES[]` (제목·메타·포인트·본문·연결 ETF)
- 커뮤니티: 게시물·답글·좋아요·인용 리포스트·투표(`pollVotes`)
- 알림: 종류별 목록·배지
- 차트: `CFG` range 별 계수(1D·1W·1M·6M·1Y·5Y·MAX)

## 미결

1. 잘린 스크립트 뒤쪽(목 데이터 나머지·핸들러) 확보 방법 — Claude Design 에서 파일 내보내기.

## 2단계 점검 (2026-09-28)

mock 으로 전 화면 관통 완료. 링크 대상은 전부 존재하는 라우트다.

### 반응 없는 것 (보류)
- AI 분석 탭 날짜 스트립 좌우 화살표: 주 이동 없음 (mock 이 1주만 있음)
- 테마 분석 우상단 공유 아이콘
- 게시물 액션의 리포스트(인용 리포스트 글쓰기 미구현)
- 관심 편집 드래그 정렬(손잡이만), 온보딩 마르키·등장 애니메이션, 테마 선택 사진(아이콘 대체)
- 탐색 안의 검색 상태(정렬 칩·결과 없음) — 전역 검색으로 대체

### 데이터 없을 때 빈 화면이 되는 경로
- 요인 상세·요인 지표: 반도체TOP10·K방산 일부 축만 데이터 있음. 나머지 축은 헤더만 뜬다 → "준비 중" 상태 화면 필요
- 테마 분석: AI·반도체·방산만 상세 있음. 나머지 테마는 네비만 뜬다
- ETF 상세 AI 분석·종목정보·오늘 움직임: 데이터 로딩 전 빈 화면(스켈레톤 없음)
- 스토리: 관심 ETF 중 데이터 있는 종목만 큐에 들어감 (반도체TOP10·K방산)

### 빈 상태 있음
홈(관심 0), 관심(그룹 비었음), 검색(결과 없음), 온보딩 ETF(검색 결과 없음), 알림(종류 없음), 게시물(답글 0), 커뮤니티 프로필(글 0), 커뮤니티 피드(내 관심 0), 이슈(내 관심 0)

### 아직 없는 화면 (인터랙션 기준)
흐름이 끊기는 것
- 이메일 회원가입·비밀번호 재설정 (로그인의 이메일 폼은 mock 로그인으로 통과)
- 비로그인 게스트 유도 시트 (관심·투표·글쓰기 진입 시)
- 인용 리포스트 글쓰기 (글쓰기 시트의 인용 박스)
- 타인 프로필 (게시물 작성자 탭)
- 게시물 신고 사유 선택 (현재 토스트만)
- 알림 설정 상세 (계정에 토글 하나)
- 관심 편집 그룹 이름 변경 (생성·삭제만 있음)

상태 화면
- 로딩 스켈레톤, 네트워크 오류·재시도, 분석 미발행(08:30 이전) 안내
- 스플래시(폰트 로드 중 흰 화면)·온보딩 재방문
- 요인·테마 "준비 중" 상태

금융 앱 필수
- 투자 유의·면책 고지 (첫 AI 분석 진입 동의 또는 분석 하단)
- 약관·개인정보 처리방침·회원 탈퇴

### 다음 단계로 넘길 것
- 3단계 API 계약: `src/api/client.ts` 인터페이스 12개(etf·watch·theme·explore·onboarding·analysis·home·community·issue·user·story·notification)가 출발점
- mock 데이터는 반도체TOP10·K방산 두 종목 중심. 나머지 4종은 기본값
