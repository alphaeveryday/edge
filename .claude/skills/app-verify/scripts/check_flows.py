"""흐름 파일 검사: 자동 검증은 읽기만 한다(2026-11-25 junyoung 결정 "가").

앱은 운영 API 를 쓴다. 로그인·투표·글쓰기·좋아요 같은 쓰기 동작을 흐름이 누르면 운영 데이터가 생긴다.
이 검사는 쓰기 버튼을 누르거나 글자를 입력하는 흐름, 앱 데이터를 지우는 흐름(게스트가 새로 생긴다)을 막는다.
예외: onboarding 태그 흐름만 clearState 와 온보딩 완료를 허용한다(ONBOARDING=1 일 때만 돈다).

사용: python3 check_flows.py <flows 디렉터리>   실패하면 종료 코드 1.
"""
import pathlib
import sys

import yaml

# 누르면 운영 데이터가 생기거나 계정 상태가 바뀌는 버튼 글자. 새 쓰기 버튼이 생기면 여기에 더한다.
WRITE_TEXTS = ["좋아요", "투표", "글쓰기", "등록", "답글", "댓글", "로그인", "회원가입", "가입", "로그아웃", "탈퇴",
               "삭제", "저장", "그룹 만들기", "담기", "관심 추가", "편집", "신고", "차단", "보내기", "전송",
               "산다", "기다린다", "판다"]
FORBIDDEN_COMMANDS = {"inputText", "inputRandomText", "inputRandomEmail", "inputRandomPersonName", "inputRandomNumber",
                      "eraseText", "clearState", "clearKeychain", "pasteText"}
ONBOARDING_ONLY = {"clearState"}
ONBOARDING_WRITE_TEXTS = {"전망 보기"}  # 온보딩 완료 = 게스트 생성


def tap_text(arg):
    if isinstance(arg, str):
        return arg
    if isinstance(arg, dict):
        return arg.get("text") or arg.get("id") or ""
    return ""


def walk(cmds, path, onboarding, errs):
    for c in cmds or []:
        if not isinstance(c, dict):
            continue
        for name, arg in c.items():
            if name in FORBIDDEN_COMMANDS and not (onboarding and name in ONBOARDING_ONLY):
                errs.append(f"{path}: '{name}' 금지 — 읽기 전용 검증")
            if name == "launchApp" and isinstance(arg, dict) and arg.get("clearState") and not onboarding:
                errs.append(f"{path}: launchApp clearState 금지 — 앱 데이터를 지우면 운영에 게스트가 새로 생긴다")
            if name in ("tapOn", "longPressOn", "doubleTapOn"):
                if isinstance(arg, dict) and "point" in arg:
                    errs.append(f"{path}: '{name}: point' 금지 — 글자 없는 버튼(관심 하트 등)은 쓰기인지 검사할 수 없다")
                    continue
                t = tap_text(arg)
                if onboarding and t in ONBOARDING_WRITE_TEXTS:
                    continue
                hit = [w for w in WRITE_TEXTS if w in t]
                if hit:
                    errs.append(f"{path}: '{name}: {t}' 는 쓰기 버튼({', '.join(hit)}) — 사람이 확인할 동작")
            if name == "runFlow" and isinstance(arg, dict) and "commands" in arg:
                walk(arg["commands"], path, onboarding, errs)
            if name == "repeat" and isinstance(arg, dict):
                walk(arg.get("commands"), path, onboarding, errs)


def main(root):
    errs = []
    files = sorted(pathlib.Path(root).rglob("*.yaml"))
    if not files:
        errs.append(f"{root}: 흐름 파일이 없다")
    for f in files:
        docs = list(yaml.safe_load_all(f.read_text()))
        cfg, cmds = (docs[0] or {}, docs[1]) if len(docs) > 1 else ({}, docs[0])
        walk(cmds, f.name, "onboarding" in (cfg.get("tags") or []), errs)
    for e in errs:
        print("FLOW_CHECK_FAIL", e)
    print(f"FLOW_CHECK {'FAIL' if errs else 'OK'} ({len(files)}개 흐름)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
