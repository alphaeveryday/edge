"""check_flows.py 가 쓰기 흐름을 실제로 막는지 확인한다. 사용: python3 check_flows_test.py

막아야 할 사례는 "종료 코드 1 + FLOW_CHECK_FAIL 줄"을 함께 요구한다. 검사기가 예외로 죽어도 종료 코드는 1 이라서
코드만 보면 '막았다'와 '죽었다'를 구분할 수 없다.
사례 대부분은 2026-11-25 Opus 리뷰가 실제로 검사를 통과시킨 우회다.
"""
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
H = "appId: x\n---\n"
BLOCK = {
    "like.yaml": H + '- tapOn: "좋아요"\n',
    "vote.yaml": H + '- tapOn:\n    text: "산다"\n',
    "type.yaml": H + '- inputText: "hi"\n',
    "clear-dict.yaml": H + '- launchApp:\n    clearState: true\n',
    "clear-bare.yaml": H + "- clearState\n",
    "erase-bare.yaml": H + "- eraseText\n",
    "point.yaml": H + '- tapOn:\n    point: "90%,5%"\n',
    "id.yaml": H + '- tapOn:\n    id: "heart"\n',
    "index-only.yaml": H + '- tapOn:\n    index: 3\n',
    "relative.yaml": H + '- tapOn:\n    text: "KODEX 반도체"\n    rightOf: "x"\n',
    "regex.yaml": H + '- tapOn: "좋.요"\n',
    "regex2.yaml": H + '- tapOn: ".*아요"\n',
    "var.yaml": "appId: x\nenv:\n  B: 좋아요\n---\n- tapOn: ${B}\n",
    "confirm.yaml": H + '- tapOn: "확인"\n',
    "readall.yaml": H + '- tapOn: "모두 읽음"\n',
    "post.yaml": H + '- tapOn: "게시"\n',
    "finish-onb.yaml": H + '- tapOn: "전망 보기"\n',
    "nested.yaml": H + '- runFlow:\n    when:\n      visible: "a"\n    commands:\n      - tapOn: "글쓰기"\n',
    "hook.yaml": 'appId: x\nonFlowStart:\n  - tapOn: "좋아요"\n---\n- back\n',
    "retry.yaml": H + '- retry:\n    maxRetries: 1\n    commands:\n      - tapOn: "좋아요"\n',
    "eval.yaml": H + "- evalScript: ${http.post('https://etforca.edgesignal.dev/api/v1/x')}\n",
    "runscript.yaml": H + "- runScript: x.js\n",
    "openlink.yaml": H + "- openLink: etforca://watch/edit\n",
    "swipe.yaml": H + '- swipe:\n    from: "순서 바꾸기"\n    direction: UP\n',
    "yml.yml": H + '- tapOn: "좋아요"\n',
    "outside.yaml": H + "- runFlow: ../../outside.yaml\n",
    "sub-onboarding-tag.yaml": H + "- runFlow: common/reset.yaml\n",
}
EXTRA_FILES = {"common/reset.yaml": "appId: x\ntags: [onboarding]\n---\n- launchApp:\n    clearState: true\n"}
PASS = {
    "onboarding.yaml": H + '- launchApp:\n    clearState: true\n- tapOn: "전망 보기"\n- tapOn: "확인했어요"\n',
    "read.yaml": H + '- launchApp:\n    stopApp: true\n    permissions:\n      all: unset\n- tapOn: "KODEX 반도체"\n'
                     '- tapOn:\n    text: "종목정보"\n    index: 0\n- extendedWaitUntil:\n    visible: "구성 해석|히트맵"\n'
                     '    timeout: 1000\n- takeScreenshot: x\n- back\n',
}


def run(files, target):
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "flows"
        for name, body in {**files, **EXTRA_FILES}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body)
        r = subprocess.run([sys.executable, HERE / "check_flows.py", root, root / target], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


bad = 0
for name, body in BLOCK.items():
    rc, out = run({name: body}, name)
    ok = rc == 1 and "FLOW_CHECK_FAIL" in out and "Traceback" not in out
    bad += not ok
    print("PASS" if ok else "FAIL", name, "막아야 함")
for name, body in PASS.items():
    rc, out = run({name: body}, name)
    ok = rc == 0
    bad += not ok
    print("PASS" if ok else "FAIL", name, "통과해야 함", "" if ok else out.strip().splitlines()[0])
print(f"{len(BLOCK) + len(PASS) - bad}/{len(BLOCK) + len(PASS)} 통과")
sys.exit(1 if bad else 0)
