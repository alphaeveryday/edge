---
name: app-verify
description: ETF Orca 앱(apps/etforca/app-ui)을 Android 에뮬레이터에서 실제로 켜고 Maestro 로 사용자처럼 눌러 보며 스크린샷·로그 증거를 남긴다. 앱 화면·동작을 바꾼 뒤 "앱에서 확인해줘", "실제로 돌려봐", "스크린샷 찍어줘", app-bugfix 의 재현·확인 단계, app-design 의 화면 비교 단계에서 쓴다. 웹(etforca 사이트)·API 단위 테스트에는 쓰지 않는다.
---

# app-verify — 앱을 실제로 켜서 확인한다

타입체크·단위 테스트는 "코드가 의도대로 갈라지는가"만 보여 준다. 화면이 뜨는지, 버튼이 눌리는지, 데이터가 보이는지는 앱을 켜야 안다. 이 스킬은 그 확인을 스크립트로 만든다. 사람은 코드를 다시 읽지 않고 이 스킬이 남긴 스크린샷을 본다.

## 지켜야 할 것

- **읽기만 한다.** 앱은 운영 API(`etforca.edgesignal.dev`)를 쓴다. 로그인·회원가입·투표(산다·기다린다·판다)·글쓰기·답글·좋아요·관심 하트·그룹 편집은 운영 데이터를 만든다. 자동 흐름에 넣지 않고 사람에게 확인을 넘긴다(2026-11-25 결정). `scripts/check_flows.py` 가 이런 흐름을 막고 `run.sh` 가 매번 먼저 돌린다.
- **앱 데이터를 지우지 않는다.** 지우면 기기 ID 가 새로 생겨 운영 서버에 게스트가 하나 더 생긴다. 설치는 `adb install -r` 로만 한다. 예외는 `flows/onboarding.yaml`(onboarding 태그) 하나다.
- **실제 분석 키를 넣지 않는다.** 검증 빌드는 PostHog·Meta 키가 비어 있고 Firebase 는 가짜 설정이다. 시험 기기 데이터가 지표에 섞이지 않게 한다.
- **증거가 없으면 통과가 아니다.** "아마 될 것", 다른 화면에서 확인, 흐름 일부만 실행은 실패로 보고한다.

## 준비 (기기마다 1회)

```bash
S=.claude/skills/app-verify/scripts
$S/install.sh                  # JDK 17·Android SDK·AVD orca35·Maestro 2.12.1 → $MOBILE_ENV (기본 /mnt/nvme/gitproject/mobile-env, 약 15GB)
```

`$MOBILE_ENV/secrets/kakao.env`(선택)에 `KAKAO_NATIVE_APP_KEY=` 를 두면 카카오 SDK 초기화가 실제 키로 된다. 없으면 빈 값으로 빌드된다.

## 실행

```bash
S=.claude/skills/app-verify/scripts
$S/build.sh apps/etforca/app-ui   # 검증용 release APK (x86_64, JS 포함, 10~15분). MODE=mock 이면 가짜 API 빌드
$S/launch.sh                      # adb 서버·에뮬레이터(systemd 유닛) 띄우고 APK 설치. READY 가 나오면 준비 완료
$S/doctor.sh                      # 읽기 전용 점검. FAIL 이 하나라도 있으면 조작하지 않는다
$S/run.sh                         # flows/ 전체(온보딩 제외). 흐름 하나만: run.sh .claude/skills/app-verify/flows/home.yaml
$S/cleanup.sh                     # 띄운 유닛만 끈다. 증거·앱 데이터는 남는다
```

- 새 기기(앱 데이터 없음)는 홈이 아니라 온보딩부터 뜬다. 처음 한 번 `ONBOARDING=1 $S/run.sh .claude/skills/app-verify/flows/onboarding.yaml` 로 온보딩을 끝낸다. 운영에 게스트 1명 + 관심 ETF(KODEX 반도체) 1개가 생긴다. 이후 흐름은 이 상태를 전제로 한다.
- `launch.sh` 는 adb 서버를 먼저 상시 실행(`mobile-adb` 유닛)한다. 셸마다 adb 서버가 새로 뜨고 죽으면 에뮬레이터 연결이 끊겨 Maestro 가 `device offline` 으로 실패한다. 그래도 끊기면 `systemctl --user stop mobile-emulator` 후 `launch.sh` 로 에뮬레이터를 다시 켠다.

## 증거

`run.sh` 가 `$MOBILE_ENV/evidence/<시각>-<설치된 APK 커밋>/` 에 남긴다.

| 파일 | 내용 |
|---|---|
| `maestro/**/takeScreenshot/*.png` | 흐름이 찍은 화면. 이름은 흐름의 `takeScreenshot` 값 |
| `maestro/**/screenshots/` | 실패한 단계의 화면(Maestro 자동) |
| `report.xml` | 흐름별 통과·실패(JUnit) |
| `maestro.txt` | 단계별 실행 기록 |
| `logcat.txt`, `app-errors.txt` | 기기 로그 전체, 그중 JS·런타임 오류 줄 |

증거 기준:
- 사용자가 가는 길로 간다. 내부 함수·테스트 전용 경로로 상태를 만들지 않는다.
- 결과 화면만이 아니라 누른 동작과 그 뒤 상태를 남긴다.
- PR 에는 바뀐 화면의 스크린샷과 `report.xml` 요약(통과 수/전체), `app-errors.txt` 줄 수를 붙인다.
- 폴더 이름의 커밋이 PR 의 커밋과 같은지 확인한다. 다르면 그 증거는 다른 코드의 것이다. `-dirty` 는 커밋 안 된 변경이 있는 빌드다.

## 흐름 쓰기

- 흐름은 `flows/*.yaml`, 하위 흐름은 `flows/common/`. 시작은 `runFlow: common/launch.yaml`(앱을 다시 켜고 홈까지 기다림).
- 글자로 누른다(`tapOn: "종목정보"`). 좌표(`point`)는 쓰지 않는다. 글자 없는 버튼은 쓰기인지 검사할 수 없다.
- 운영 데이터는 매일 바뀐다. 가격·글 내용이 아니라 구조(탭·카드 제목·빈 상태 문구)를 확인한다.
- `launchApp` 에는 `permissions: {all: unset}` 을 준다. 기본값(all: allow)은 이 에뮬레이터에서 adb 연결을 끊었다.
- 새 흐름을 더하면 `features/` 에 기능 파일을 쓰고 `features/README.md` 표에 넣는다. 새 쓰기 버튼을 찾으면 `check_flows.py` 의 `WRITE_TEXTS` 에 더하고 `check_flows_test.py` 에 사례를 더한다.

## 기능 지도

`features/README.md`. 기능마다 하위 기능, 가는 길(사용자 시점), Maestro 조작, 주의할 점을 적는다. 지도에 있는 기능은 전부 흐름으로 확인한다. 편한 진입점 하나만 확인한 것은 불완전한 증명이다. 화면 정본은 `apps/etforca/app-ui/screens.md`.

## 규칙과 그 규칙을 지키게 하는 장치

같은 지적이 두 번 나오면 문서 한 줄이 아니라 검사로 만든다(app-bugfix 의 "지적 → 검사" 참고).

| 규칙 | 지키게 하는 것 |
|---|---|
| app-ui 를 바꾼 PR 은 Android 빌드가 통과해야 한다 | `.github/workflows/app-android-build.yml` (#1605 오류가 4주간 숨어 있었던 일에서 나옴) |
| 자동 검증은 운영 데이터를 만들지 않는다 | `scripts/check_flows.py` (+ `check_flows_test.py`), `run.sh` 가 매번 실행 |
| 검증 빌드는 실제 분석·Firebase 키를 쓰지 않는다 | `scripts/build.sh` 가 키를 비우고 `.github/scripts/write_fake_firebase.py` 로 가짜 설정을 쓴다 |
| 기능 지도와 앱이 어긋나지 않는다 | 사람이 주기적으로 지도의 기능을 전부 실행해 맞춘다(pstack maintain-verification-skill 방식). 아직 자동 장치 없음 |

## 출처

Lauren Tan 의 pstack `create-verification-skill`·`maintain-verification-skill` 구조(Launch·Doctor·Drive·Evidence·Cleanup, 기능 지도)를 edge 앱에 맞췄다. https://github.com/cursor/plugins/tree/main/pstack
