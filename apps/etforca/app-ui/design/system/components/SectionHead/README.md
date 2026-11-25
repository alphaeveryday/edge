화면 안 한 구역의 제목 줄이다.

## 언제 쓰나

- 홈, 탐색, 검색, 종목정보처럼 여러 구역이 세로로 쌓이는 화면에서 각 구역의 머리에 둔다.
- 오른쪽에는 셋 중 하나만 둔다: 더 보기 같은 행동, 개수 같은 보조 글자, 기준 시각 배지.
- 투자 유의 사항을 붙여야 하는 구역은 제목 옆에 정보 아이콘을 둔다.

## 수치

- 좌우 여백 `page-x`, 제목은 `sectionTitle`.
- 행동: 12.5px 굵기 700에 `primary`, 14px 화살표. 보조 글자: `caption`에 `textMuted`.
- 배지: 11.5px 굵기 700에 `textMuted`, `surface` 바탕, 여백 5px 9px, 모서리 `radius-tag`.
- 정보 아이콘 18px에 `textFaint`.

## 쓰는 쪽이 정하는 것

`title`, 그리고 `actionLabel`과 `onAction`, `meta`, `badge` 중 하나. 정보 시트를 열려면 `onInfo`.

정적 재현이다. `src/components/ui/SectionHead.tsx`의 수치를 옮겼다.
