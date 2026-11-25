"""흐름 파일 검사: 자동 검증은 읽기만 한다(2026-11-25 junyoung 결정 "가").

앱은 운영 API 를 쓴다. 로그인·투표·글쓰기·좋아요 같은 쓰기 동작을 흐름이 하면 운영 데이터가 생기고,
앱 데이터를 지우면 기기 ID 가 새로 생겨 운영에 게스트가 하나 더 생긴다.

허용 목록 방식이다. 아래 READ_COMMANDS 에 있는 명령만 쓸 수 있고, 나머지(clearState·inputText·swipe·
openLink·evalScript·runScript·retry·repeat·pressKey 등)는 모두 실패다. 누르기는 글자 그대로 쓴 text 하나로만
하고(정규식·변수·id·좌표·상대 위치 불가), 쓰기 버튼 글자는 막는다.
예외는 flows/onboarding.yaml 파일 하나다: 그 파일 안에서만 launchApp clearState 와 "전망 보기"(온보딩 완료)를 허용한다.

사용: python3 check_flows.py <flows 루트> [검사할 흐름 파일·디렉터리 ...]   (생략하면 루트 전체)
실패하면 종료 코드 1. run.sh 가 실제로 돌릴 경로를 그대로 넘긴다.
"""
import pathlib
import re
import sys

import yaml

READ_COMMANDS = {"launchApp", "stopApp", "assertVisible", "assertNotVisible", "extendedWaitUntil",
                 "waitForAnimationToEnd", "takeScreenshot", "tapOn", "back", "scroll", "scrollUntilVisible", "runFlow"}
CONFIG_KEYS = {"appId", "name", "tags", "env"}
TAP_KEYS = {"text", "index", "label", "waitToSettleTimeoutMs", "retryTapIfNoChange"}
LAUNCH_KEYS = {"appId", "stopApp", "permissions", "clearState", "label"}
# 이 글자가 들어간 버튼은 누르지 않는다. 새 쓰기 버튼이 생기면 더하고 check_flows_test.py 에 사례를 더한다.
WRITE_SUBSTR = ["좋아요", "투표", "글쓰기", "답글", "댓글", "로그인", "회원가입", "로그아웃", "탈퇴", "모두 읽음",
                "산다", "기다린다", "판다", "전망 보기", "편집", "신고", "차단", "보내기", "전송", "그룹 만들기",
                "관심 추가", "순서", "삭제"]
# 짧아서 다른 글자에 섞이는 쓰기 버튼은 글자 전체가 같을 때만 막는다(예: "확인" 은 막고 "확인했어요" 는 허용).
WRITE_EXACT = {"확인", "게시", "등록", "저장", "담기", "완료", "적용"}
ONBOARDING_FILE = "onboarding.yaml"
ONBOARDING_WRITE = {"전망 보기"}
REGEX_META = re.compile(r"[.*+?\[\](){}|\\^$]")


class Checker:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.errs = []

    def err(self, path, msg):
        self.errs.append(f"{path.relative_to(self.root) if path.is_relative_to(self.root) else path}: {msg}")

    def load(self, path):
        docs = list(yaml.safe_load_all(path.read_text()))
        if len(docs) == 1:
            return {}, docs[0]
        if len(docs) == 2:
            return docs[0] or {}, docs[1]
        raise ValueError(f"YAML 문서가 {len(docs)}개")

    def check_file(self, path, seen):
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        if not path.is_relative_to(self.root):
            self.err(path, f"flows 루트({self.root}) 밖의 흐름은 검사·실행하지 않는다")
            return
        try:
            cfg, cmds = self.load(path)
        except Exception as e:  # noqa: BLE001 — 읽지 못한 흐름은 통과가 아니라 실패다
            self.err(path, f"YAML 을 읽지 못했다: {e}")
            return
        for key in cfg:
            if key not in CONFIG_KEYS:
                self.err(path, f"설정 '{key}' 금지 — onFlowStart·onFlowComplete 등은 명령을 숨길 수 있다")
        for v in (cfg.get("env") or {}).values():
            if isinstance(v, str) and (any(w in v for w in WRITE_SUBSTR) or v in WRITE_EXACT):
                self.err(path, f"env 값 '{v}' 가 쓰기 버튼 글자다")
        exempt = path == (self.root / ONBOARDING_FILE).resolve()
        if not isinstance(cmds, list):
            self.err(path, "명령 목록이 아니다")
            return
        self.walk(cmds, path, exempt, seen)

    def walk(self, cmds, path, exempt, seen):
        for c in cmds:
            if isinstance(c, str):
                c = {c: None}
            if not isinstance(c, dict) or len(c) != 1:
                self.err(path, f"알 수 없는 명령 형태: {c!r}")
                continue
            (name, arg), = c.items()
            if name not in READ_COMMANDS:
                self.err(path, f"'{name}' 금지 — 허용 목록(읽기 명령) 밖")
                continue
            getattr(self, f"cmd_{name}", lambda *a: None)(arg, path, exempt, seen)

    def cmd_launchApp(self, arg, path, exempt, seen):
        if isinstance(arg, dict):
            extra = set(arg) - LAUNCH_KEYS
            if extra:
                self.err(path, f"launchApp 옵션 {sorted(extra)} 금지")
            if arg.get("clearState") and not exempt:
                self.err(path, "launchApp clearState 금지 — 앱 데이터를 지우면 운영에 게스트가 새로 생긴다")

    def cmd_tapOn(self, arg, path, exempt, seen):
        if isinstance(arg, str):
            arg = {"text": arg}
        if not isinstance(arg, dict) or not isinstance(arg.get("text"), str):
            self.err(path, f"tapOn 은 글자(text)로만 누른다: {arg!r}")
            return
        extra = set(arg) - TAP_KEYS
        if extra:
            self.err(path, f"tapOn 옵션 {sorted(extra)} 금지 — id·좌표·상대 위치는 쓰기 버튼인지 검사할 수 없다")
        t = arg["text"]
        if "${" in t or REGEX_META.search(t):
            self.err(path, f"tapOn '{t}' — 변수·정규식 글자 금지(글자 그대로 쓴다)")
            return
        if exempt and t in ONBOARDING_WRITE:
            return
        hit = [w for w in WRITE_SUBSTR if w in t] + ([t] if t in WRITE_EXACT else [])
        if hit:
            self.err(path, f"tapOn '{t}' 는 쓰기 버튼({', '.join(hit)}) — 사람이 확인할 동작")

    def cmd_runFlow(self, arg, path, exempt, seen):
        if isinstance(arg, str):
            arg = {"file": arg}
        if not isinstance(arg, dict):
            self.err(path, f"runFlow 형태 금지: {arg!r}")
            return
        extra = set(arg) - {"file", "commands", "when", "label"}
        if extra:
            self.err(path, f"runFlow 옵션 {sorted(extra)} 금지")
        if "file" in arg:
            self.check_file(path.parent / arg["file"], seen)
        if "commands" in arg:
            # 같은 파일 안의 명령이라 예외도 그 파일의 것을 그대로 쓴다
            self.walk(arg["commands"], path, exempt, seen)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    ch = Checker(argv[0])
    targets = [pathlib.Path(p) for p in argv[1:]] or [ch.root]
    files = []
    for t in targets:
        if t.is_dir():
            files += sorted(p for p in t.rglob("*") if p.suffix in (".yaml", ".yml"))
        elif t.exists():
            files.append(t)
        else:
            ch.err(t, "없는 경로")
    if not files and not ch.errs:
        ch.err(ch.root, "흐름 파일이 없다")
    seen = set()
    for f in files:
        ch.check_file(f, seen)
    for e in ch.errs:
        print("FLOW_CHECK_FAIL", e)
    print(f"FLOW_CHECK {'FAIL' if ch.errs else 'OK'} ({len(seen)}개 흐름)")
    return 1 if ch.errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
