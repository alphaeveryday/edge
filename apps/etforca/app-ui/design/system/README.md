# ETF Orca

ETF Orca는 ETF의 가격이 왜 움직였는지와 오늘의 전망을 읽는 모바일 앱이다. 이 문서는 앱 코드(`apps/etforca/app-ui`)의 테마 파일에서 그대로 옮긴 색, 글자, 간격, 모서리, 그림자와 핵심 컴포넌트의 쓰임을 정리한다. 값의 원천은 코드이고, 이 문서는 그 사본이다.

앱의 화면 34개는 라이트와 다크로 그대로 옮겨 두었다. 목록과 흐름은 「화면」 섹션에 있다.

웹 포트폴리오 진단과 금융사 콘솔은 다른 토큰을 쓴다. 차이와 통합 제안은 「제품별 차이」 섹션에 있다.

## 글쓰기

- **해요체로 짧게 쓴다.** "찾는 ETF가 없어요", "전망은 매일 장 시작 전에, 가격 변동 설명은 가격이 움직일 때 갱신돼요."
- **행동이 아니라 사실을 말한다.** 사라, 팔아라가 아니라 무엇이 왜 움직였는지를 쓴다. 면책 문구는 "매수·매도 권유가 아니며, 투자 판단과 책임은 이용자 본인에게 있어요."를 그대로 쓴다.
- **전망은 다섯 단계 이름만 쓴다.** 강력 상승, 상승, 중립, 하락, 강력 하락. 다른 말로 바꾸지 않는다.
- **나열은 가운뎃점으로 묶는다.** "ETF·테마 검색", "매수·매도".
- **숫자는 mono 스타일로 쓴다.** 종목 코드와 등락률처럼 자릿수를 맞춰 읽는 값에 쓴다.

## 색

색은 light와 dark 두 테마에 같은 이름으로 있다. 이름은 역할이고, 화면 코드는 이름만 쓴다.

**면과 선.** 화면 바탕은 `bg`, 그 위의 카드는 `card`, 입력창과 선택된 칩 같은 채움은 `surface`다. 영역을 나누는 선은 `line`, 테두리로 보여야 하는 선은 `lineStrong`이다.

**글자.** `text` → `textSub` → `textMuted` → `textFaint` → `textDisabled` 순으로 약해진다. 읽어야 하는 문장은 `textMuted`까지만 쓴다. `textFaint`는 플레이스홀더와 아이콘, `textDisabled`는 비활성 글자와 목록 화살표에만 쓴다.

**브랜드.** `primary`는 주요 버튼, 더 보기 링크, 알림 아이콘에 쓴다. 옅은 면이 필요하면 `primarySoft`, 그 테두리는 `primaryTint`다. 한 화면에서 `primary` 채움은 주요 행동 하나에만 쓴다.

**방향.** 상승은 `up`(빨강), 하락은 `down`(파랑), 중립은 `neutral`이다. 한국 시장 관례를 따른다. 색만으로 방향을 전하지 않는다. 항상 기호(▲ ■ ▼)나 글자를 함께 둔다. 색 바탕 위의 글자는 `upDeep`, `downDeep`, `neutralDeep`을 쓰고, 바탕은 `signalAlpha-*`나 `upSoft`, `downSoft`를 쓴다.

**상태.** 완료는 `success`, 주의는 `warn`과 `warnDeep`이다. `up`은 danger 버튼과 새 글 점에도 쓰이므로, 한 화면에서 "상승"과 "위험"이 섞이지 않게 한다.

**차트.** 히트맵은 `heat-*`, 게이지는 `gaugeStrongDown` · `downLight` · `lineStrong` · `upLight` · `up` 순서, 20일 이동평균선은 `chartMa20`이다.

## 글자

글꼴은 Pretendard 하나에 굵기 다섯 가지(400, 500, 600, 700, 800)이고, 숫자에는 JetBrains Mono를 쓴다. 제목과 버튼은 모두 800이다. 크기는 역할 이름으로만 고른다.

| 역할 | 스타일 |
| --- | --- |
| 화면 제목 | `pageTitle` 24/30 |
| 구역 제목 | `sectionTitle` 20 |
| 시트 제목 | `sheetTitle` 19/25 |
| 카드 제목 | `cardTitle` 17/23 |
| 내비게이션 제목 | `navTitle` 16 |
| 버튼 | `button` 15.5 |
| 목록 이름 | `listLabel` 15.5 |
| 탭, 칩 | `tab` 14 |
| 스티커 | `sticker` 12.5 |
| 본문 | `body` 14/22 |
| 캡션 | `caption` 12.5 |
| 숫자 | `mono` 12.5 |

제목은 자간을 좁힌다(-0.3px ~ -0.7px). 본문과 캡션은 자간을 건드리지 않는다.

## 간격과 모서리

간격은 `space-xs` 4, `space-sm` 8, `space-md` 12, `space-lg` 16, `space-xl` 20, `space-xxl` 28 여섯 단계다. 화면 좌우 여백은 `page-x` 20으로 고정이고, 제목과 구역 머리와 본문이 모두 이 선에 맞는다.

모서리는 크기가 커질수록 둥글어진다. 배지 `radius-tag` 8, 칩 `radius-control` 10, 입력창 `radius-field` 12, 버튼 `radius-button` 14, 카드 `radius-card` 16, 시트 `radius-sheet` 22. 점과 알약은 `radius-pill`이다.

## 그림자

면은 그림자가 아니라 색(`card`, `surface`)으로 구분한다. 그림자는 화면 위에 뜨는 것에만 쓴다. 작은 요소 `shadow-floating`, 둥근 버튼 `shadow-fab`, 토스트 `shadow-toast`, 바텀시트 `shadow-sheet`.

## 아이콘

Lucide 1.54의 선 아이콘을 쓴다(ISC 라이선스). 크기는 15~24px, 색은 글자색 토큰을 따른다. 목록 행의 아이콘은 19px에 `textSub`, 검색창은 18px에 `textFaint`다. 업종 아이콘은 앱이 따로 그린 것을 쓰며 이 시스템에는 아직 없다.

## 로고

로고는 흰 범고래 안에 오르는 막대 세 개가 있는 마크다. 앱 아이콘은 검정 바탕에 흰 마크다. 마크는 흰색 한 가지만 있으므로 밝은 바탕에는 그대로 올릴 수 없다. 자세한 규칙은 Logos 묶음의 안내에 있다.

## 다크 모드

모든 색 토큰에 dark 값이 있다. dark에서 `primary`는 더 밝은 보라(#7B74F2)가 되고, `upDeep`과 `downDeep`은 더 어두워지지 않고 더 밝아진다. 화면 코드는 테마를 직접 분기하지 않고 토큰 이름만 쓴다.

## 접근성 점검

코드의 값을 그대로 옮겼고, 아래 조합은 대비 기준(작은 글자 4.5:1)에 못 미친다. 값을 바꾸려면 코드에서 바꾼다.

- light: `surface` 위 `textMuted` 4.2:1
- light: `bg` 위 `up`, `down` 글자 3.7:1. 작은 글자에는 `upDeep`, `downDeep`을 쓴다.
- light와 dark: `up` 바탕 위 `onPrimary` 3.7:1, 3.4:1 (danger 버튼)
- dark: `primary` 바탕 위 `onPrimary` 3.7:1 (주요 버튼)

## 옮기지 않은 것

- 화면은 웹 빌드의 결과물을 떠낸 정지 화면이다. 값이 박혀 있어 토큰과 연결돼 있지 않다.
- 컴포넌트는 React Native 코드라 실행 가능한 번들로 옮기지 않았다. 아래 일곱 개는 코드의 수치를 읽어 HTML로 다시 그린 정적 재현이다: CtaButton, Chip, Sticker, ListRow, TabItem, SearchField, SectionHead.
- 옮기지 않은 컴포넌트: Avatar, BottomBar, BottomSheet, Dialog, IconButton, LinkRow, NavBar, PageScroll, PageTitle, PostActions, Quote, SectorIcon, SheetHead, TabBarLine, ToggleRow, Toast, TopBar (`src/components`).
- 그림자는 React Native 속성을 웹 box-shadow로 바꿔 적었다. 번짐이 기기와 조금 다를 수 있다.
- 스플래시 이미지와 안드로이드 적응형 아이콘은 넣지 않았다.
