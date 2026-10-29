# app-ui

ETF Orca B2C 앱. Expo(React Native) + Expo Router.

## 이 모듈만 다른 점

- **pnpm 워크스페이스 밖의 독립 패키지**다. Metro 가 pnpm 심볼릭 링크 구조와 충돌해서 `pnpm-workspace.yaml` 에서 제외했고, 의존성은 이 폴더의 `package-lock.json`(npm)으로 관리한다. 여기서는 `pnpm` 이 아니라 `npm` / `npx expo install` 을 쓴다.
- **Expo Go 를 쓰지 않는다.** 처음부터 development build(`expo-dev-client`)다. `npm run ios` / `npm run android` 가 네이티브 빌드를 만든다(iOS 는 Xcode 필요).
- 화면은 `src/api` 의 클라이언트 인터페이스만 본다. `EXPO_PUBLIC_API_MODE` 가 `mock`(기본)이면 `src/api/mock`, `http` 이면 `src/api/http` 구현을 쓴다. 택일은 `src/api/index.ts` 에서만 한다.

## 구조

```
app/            Expo Router 라우트 = 화면 (screens.md 트리)
src/api/        types · client 인터페이스 · mock 구현 · 택일 index
src/features/   도메인별 react-query 훅
src/components/ 디자인 프리미티브
src/store/      zustand (세션 등 로컬 상태)
src/theme/      토큰 (색·타이포·간격)
design/         Claude Design 원본 사본 (참고용)
modules/ads-consent/  네이티브 시작 시 통계 설정 복원과 실행 중 SDK 설정 변경
```

## 통계 수집 설정

- iOS AppDelegate와 Android Application에서 기존 PostHog 파일의 토글 값 복원. iOS Documents와 Android filesDir의 `.posthog-rn.json` 중 `v1.content.opted_out` 사용. PostHog가 있는 구성에서는 해당 SDK의 저장 유지, 없는 구성에서는 동일 파일에 토글만 갱신. 저장 형식 변경 시 네이티브 파서의 동반 점검 필요.
- Meta Core SDK 19.0.0 고정. `react-native-fbsdk-next`는 Expo 설정 플러그인만 사용하고 네이티브 자동 연결에서 제외. SDK 19의 추가 메타데이터·제품 개선용 자동 수집 비활성화.
- 네이티브 설정과 시작 코드 변경에 따른 앱 재빌드 필요. JavaScript 업데이트만으로 적용 불가. SDK의 기존 허용 값과 저장된 거부 값이 충돌하는 첫 실행의 실제 이벤트 전송은 기기 검증 대상.

## 명령

```
npm install
npm start          # 메트로
npm run typecheck
npm test          # 인증 전환·본인 글·분석 발행일 회귀 테스트
npm run test:consent-store # JDK·Swift CLI로 설정 파일 처리 검증, 네이티브 앱 빌드와 별개
npm run doctor
```
