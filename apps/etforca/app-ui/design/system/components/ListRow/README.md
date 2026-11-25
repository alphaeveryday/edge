아이콘, 이름, 값, 화살표로 이루어진 목록 한 줄이다.

## 언제 쓰나

- 메뉴와 설정처럼 눌러서 다른 화면으로 가는 항목을 나열할 때 쓴다.
- 이름만으로 뜻이 모자랄 때 `sub` 한 줄을 붙인다. 두 줄 넘는 설명은 넣지 않는다.
- 켜고 끄는 항목에는 ToggleRow를 쓴다(이 시스템에는 아직 없다).

## 수치

- 안쪽 여백 14px 8px, 요소 사이 14px. 이름은 `listLabel`, 보조 줄은 `caption`에 `textMuted`, 값은 14px `textMuted`.
- 아이콘 19px에 `textSub`, 화살표 16px에 `textDisabled`.
- 구분선은 `surface` 색 1px이고 마지막 줄에는 넣지 않는다.
- 이름과 보조 줄은 한 줄로 자른다. 누르는 동안 불투명도 0.6.

## 쓰는 쪽이 정하는 것

`label`, 아이콘 `icon`, 보조 줄 `sub`, 오른쪽 값 `value`, 화살표와 구분선 표시 여부.

정적 재현이다. `src/components/ui/ListRow.tsx`의 수치를 옮겼다.
