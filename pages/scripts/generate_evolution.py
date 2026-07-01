#!/usr/bin/env python3
"""docs/ 아키텍처 문서의 변경 커밋을 모아 발전 기록(evolution.md)을 생성한다.

repo root 에서 실행한다고 가정한다. 외부 패키지 없이 표준 라이브러리만 쓴다.

- pages/evolution.yml 의 include_paths / exclude / overrides 를 읽는다.
- include_paths 를 건드린 커밋을 git 에서 시간순으로 수집한다.
- exclude(SHA prefix) 에 걸리는 커밋은 뺀다.
- overrides(SHA prefix) 로 title/description/image 를 덮어쓴다.
- pages/docs/evolution.md 를 생성한다. 커밋이 없어도 실패하지 않는다.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path.cwd()
CONFIG_PATH = REPO_ROOT / "pages" / "evolution.yml"
OUTPUT_PATH = REPO_ROOT / "pages" / "docs" / "evolution.md"
EMPTY_MESSAGE = "No architecture evolution entries yet."
SEP = "\x1f"  # git 출력 필드 구분자


# ── evolution.yml (제한된 YAML 부분집합) 파서 ─────────────────────────────
# 지원 형태: 스칼라, `[]`/`{}`, `- item` 리스트, 들여쓰기 중첩 매핑.
# evolution.yml 스키마에 맞춘 최소 파서다(임의 YAML 을 지원하지 않는다).
def _strip_comment(line: str) -> str:
    # YAML 규칙에 가깝게: '#' 은 줄 시작이거나 앞이 공백일 때만 주석이다.
    # 따옴표는 값 시작(줄 시작·공백·':' 뒤)에서 열릴 때만 인용으로 본다 —
    # don't 같은 단어 중간 아포스트로피를 인용 시작으로 오인하지 않게 한다.
    quote = None
    out = []
    prev = ""
    for ch in line:
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (prev == "" or prev.isspace() or prev == ":"):
            quote = ch
        elif ch == "#" and (prev == "" or prev.isspace()):
            break
        out.append(ch)
        prev = ch
    return "".join(out)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_simple_yaml(text: str):
    # evolution.yml 스키마에 맞춘 최소 파서. **잘 형성된(well-formed) 입력**을 가정한다:
    # 한 블록 안에서 들여쓰기는 일관되고, 값에 백슬래시로 이스케이프한 따옴표는 쓰지
    # 않는다. 이 스키마(경로 리스트·SHA prefix·title/description/image 스칼라)는 그런
    # 경우가 없으므로 충분하다. 임의 YAML 이 필요해지면 이 파서 대신 라이브러리를 쓸 것.
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))

    pos = [0]

    def is_item(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def parse_list(list_indent: int):
        items = []
        while pos[0] < len(lines):
            indent, content = lines[pos[0]]
            if indent != list_indent or not is_item(content):
                break
            items.append(_unquote(content[1:].strip()))
            pos[0] += 1
        return items

    def parse_value(value: str, parent_indent: int):
        if value == "[]":
            return []
        if value == "{}":
            return {}
        if value != "":
            return _unquote(value)
        # 인라인 값이 비면 다음 줄들이 블록이다. YAML 은 부모 키와 '같은'
        # 들여쓰기의 블록 시퀀스(- item)도 허용하므로 >= 로 판정한다.
        if pos[0] >= len(lines):
            return {}
        next_indent, next_content = lines[pos[0]]
        if is_item(next_content) and next_indent >= parent_indent:
            return parse_list(next_indent)
        if next_indent > parent_indent:
            return parse_block(next_indent)
        return {}

    def parse_mapping(map_indent: int):
        mapping = {}
        while pos[0] < len(lines):
            indent, content = lines[pos[0]]
            if indent != map_indent or is_item(content):
                break
            match = re.match(r"^([^:]+):\s*(.*)$", content)
            if not match:
                pos[0] += 1
                continue
            key = match.group(1).strip()
            value = match.group(2).strip()
            pos[0] += 1
            mapping[key] = parse_value(value, map_indent)
        return mapping

    def parse_block(block_indent: int):
        if pos[0] >= len(lines):
            return {}
        if is_item(lines[pos[0]][1]):
            return parse_list(block_indent)
        return parse_mapping(block_indent)

    return parse_block(lines[0][0] if lines else 0)


def load_config():
    if not CONFIG_PATH.exists():
        # 설정이 없으면 조용히 빈 페이지를 내지 않고 시끄럽게 실패한다(fail loud).
        raise SystemExit(f"[evolution] 설정 파일이 없습니다: {CONFIG_PATH}")
    data = parse_simple_yaml(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    include_paths = data.get("include_paths") or []
    exclude = data.get("exclude") or []
    overrides = data.get("overrides") or {}
    # 빈 문자열 prefix 는 모든 SHA 에 매칭되므로(startswith("")) 걸러낸다.
    include_paths = (
        [p for p in include_paths if isinstance(p, str) and p.strip()]
        if isinstance(include_paths, list) else []
    )
    exclude = (
        [e for e in exclude if str(e).strip()]
        if isinstance(exclude, list) else []
    )
    overrides = (
        {k: v for k, v in overrides.items() if str(k).strip()}
        if isinstance(overrides, dict) else {}
    )
    # include_paths 가 비면 추적할 대상이 없다 — 키 오타(include_path:)나 잘못된
    # 형태로 값이 사라진 것이므로 조용히 빈 페이지를 내지 않고 실패한다(fail loud).
    # (경로는 있는데 커밋이 없는 '정상적 빈 결과'는 render 의 EMPTY_MESSAGE 로 처리.)
    if not include_paths:
        raise SystemExit(
            "[evolution] include_paths 가 비었습니다 — pages/evolution.yml 설정을 확인하세요."
        )
    return {"include_paths": include_paths, "exclude": exclude, "overrides": overrides}


def ensure_repo_root():
    # include_paths 는 root 기준 상대경로다. 다른 디렉터리에서 실행하면 git 이
    # 아무것도 못 찾아 '빈 페이지'가 조용히 배포되므로, root 가 아니면 실패한다.
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[evolution] git 저장소(repo root)에서 실행하세요: {REPO_ROOT}")
    root = Path(result.stdout.strip()).resolve()
    if root != REPO_ROOT.resolve():
        raise SystemExit(
            f"[evolution] repo root 에서 실행하세요. cwd={REPO_ROOT}, repo root={root}"
        )


# ── git ───────────────────────────────────────────────────────────────────
def git(args):
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {result.stderr.strip()}")
    return result.stdout


def collect_commits(include_paths):
    if not include_paths:
        return []
    fmt = SEP.join(["%H", "%ad", "%s"])
    out = git([
        "log", "--reverse", "--no-merges", "--date=short",
        f"--pretty=format:{fmt}", "--", *include_paths,
    ])
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, date, subject = line.split(SEP, 2)  # 제목에 SEP 가 있어도 안전
        commits.append({"sha": sha, "date": date, "subject": subject})
    return commits


def changed_files(sha, include_paths):
    out = git([
        "diff-tree", "--no-commit-id", "--name-only", "-r", "--root",
        sha, "--", *include_paths,
    ])
    return [line for line in out.splitlines() if line.strip()]


# ── 렌더링 ─────────────────────────────────────────────────────────────────
def github_base():
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    return f"https://github.com/{repo}" if repo else None


def find_override(sha, overrides):
    # 가장 구체적인(긴) prefix 가 이기도록 길이 내림차순으로 본다 —
    # '9604' 와 '9604c09' 가 함께 있어도 더 구체적인 쪽이 적용된다.
    for prefix in sorted(overrides, key=lambda p: len(str(p)), reverse=True):
        if sha.startswith(str(prefix)):
            value = overrides[prefix]
            return value if isinstance(value, dict) else {}
    return {}


def is_excluded(sha, exclude):
    return any(sha.startswith(str(prefix)) for prefix in exclude)


def render(commits, config):
    base = github_base()
    exclude = config["exclude"]
    overrides = config["overrides"]
    include_paths = config["include_paths"]

    lines = [
        "# Evolution",
        "",
        "> `docs/` 아키텍처 문서를 바꾼 커밋을 시간순으로 정리한 발전 기록입니다.",
        "> 이 페이지는 `pages/scripts/generate_evolution.py` 가 생성합니다 — 직접 편집하지 마세요.",
        "",
    ]

    rendered = 0
    for commit in commits:
        sha = commit["sha"]
        if is_excluded(sha, exclude):
            continue
        override = find_override(sha, overrides)
        title = override.get("title") or commit["subject"]
        description = override.get("description")
        image = override.get("image")

        lines.append(f"## {commit['date']} · {title}")
        lines.append("")
        if image:
            lines.append(f"![{title}]({image})")
            lines.append("")
        if description:
            lines.append(str(description))
            lines.append("")
        lines.append(f"- SHA: `{sha[:7]}`")
        files = changed_files(sha, include_paths)
        if files:
            lines.append("- 변경 파일:")
            for path in files:
                lines.append(f"    - `{path}`")
        if base:
            lines.append(f"- [GitHub에서 커밋 보기]({base}/commit/{sha})")
        lines.append("")
        rendered += 1

    if rendered == 0:
        lines.append(EMPTY_MESSAGE)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n", rendered


def main() -> int:
    ensure_repo_root()
    config = load_config()
    commits = collect_commits(config["include_paths"])
    content, rendered = render(commits, config)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    where = OUTPUT_PATH.relative_to(REPO_ROOT)
    if rendered == 0:
        print(f"[evolution] {EMPTY_MESSAGE} -> {where}")
    else:
        print(f"[evolution] {rendered}개 항목 생성 -> {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
