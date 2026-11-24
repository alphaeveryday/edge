"""check_flows.py 가 쓰기 흐름을 실제로 막는지 확인한다. 사용: python3 check_flows_test.py"""
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
CASES = {
    "like.yaml": ('appId: x\n---\n- tapOn: "좋아요"\n', True),
    "vote.yaml": ('appId: x\n---\n- tapOn:\n    text: "산다"\n', True),
    "type.yaml": ('appId: x\n---\n- inputText: "hi"\n', True),
    "clear.yaml": ('appId: x\n---\n- launchApp:\n    clearState: true\n', True),
    "point.yaml": ('appId: x\n---\n- tapOn:\n    point: "90%,5%"\n', True),
    "nested.yaml": ('appId: x\n---\n- runFlow:\n    when:\n      visible: "a"\n    commands:\n      - tapOn: "글쓰기"\n', True),
    "onboarding.yaml": ('appId: x\ntags: [onboarding]\n---\n- launchApp:\n    clearState: true\n- tapOn: "전망 보기"\n', False),
    "read.yaml": ('appId: x\n---\n- launchApp:\n    stopApp: true\n- tapOn: "KODEX 반도체"\n- tapOn: "종목정보"\n', False),
}
bad = 0
for name, (body, should_fail) in CASES.items():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / name).write_text(body)
        rc = subprocess.run([sys.executable, HERE / "check_flows.py", d], capture_output=True).returncode
    ok = (rc == 1) == should_fail
    bad += not ok
    print(("PASS" if ok else "FAIL"), name, "막아야 함" if should_fail else "통과해야 함")
sys.exit(1 if bad else 0)
