ETF와 테마를 찾는 검색 입력창이다.

## 언제 쓰나

- 탐색과 관심 ETF 추가처럼 이름으로 찾는 자리에 쓴다.
- 플레이스홀더에는 무엇을 찾을 수 있는지 적는다. 기본값은 "검색"이고, 앱은 "ETF·테마 검색"을 쓴다.

## 수치

- 높이 44px, 좌우 여백 14px, 모서리 `radius-field`, 바탕 `surface`, 테두리 없음.
- 아이콘 18px에 `textFaint`, 아이콘과 글자 사이 10px.
- 입력 글자 15px에 `text`, 플레이스홀더는 `textFaint`. 자동 교정은 끈다.

## 쓰는 쪽이 정하는 것

`value`와 `onChangeText`, `placeholder`, 화면에 들어오자마자 초점을 줄지(`autoFocus`).

정적 재현이다. `src/components/ui/SearchField.tsx`의 수치를 옮겼다.
