"""Version the actual prompt YAML files and snapshot each execution's instructions."""
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

import yaml


class PromptConflict(ValueError):
    pass


def parse_prompt(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 131072:
        raise ValueError('YAML은 128KB 이하의 문자열이어야 합니다.')
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        value = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        raise ValueError('YAML 문법 오류' + (f' · {mark.line+1}행 {mark.column+1}열' if mark else '')) from None
    if (not isinstance(node, yaml.MappingNode) or len(node.value) != 1 or not isinstance(value, dict)
            or set(value) != {'system_prompt'} or not isinstance(value['system_prompt'], str)
            or not value['system_prompt'].strip()):
        raise ValueError('중복·추가 키 없이 system_prompt에 비어 있지 않은 문자열을 입력하세요.')
    return value['system_prompt']


class PromptVersions:
    def __init__(self, sources, history):
        self.sources, self.history = Path(sources), Path(history)
        self.history.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _path(self, kind):
        if kind not in ('outlook', 'movement'):
            raise ValueError('지원하지 않는 프롬프트 종류입니다.')
        return self.sources/(kind + '.yaml')

    @staticmethod
    def _write(path, text):
        temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        try:
            temporary.write_text(text, encoding='utf-8', newline='\n')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _record(self, kind, text, note):
        return dict(version=sha256(text.encode('utf-8')).hexdigest(), yaml=text,
                    saved_at=datetime.now(timezone.utc).isoformat(), note=note)

    def read(self, kind):
        with self.lock:
            text = self._path(kind).read_text(encoding='utf-8')
            prompt = parse_prompt(text)
            record = self._record(kind, text, '현재 파일 등록 · 외부 변경 포함')
            ledger = self.history/(kind + '.json')
            history = json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else []
            if not history or history[-1]['version'] != record['version']:
                history.append(record)
                self._write(ledger, json.dumps(history, ensure_ascii=False, indent=2))
            return dict(kind=kind, version=record['version'], yaml=text, system_prompt=prompt,
                        source=str(self._path(kind)), history=list(reversed(history)))

    def save(self, kind, text, expected_version, note):
        parse_prompt(text)
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('변경 메모는 500자 이하로 입력하세요.')
        with self.lock:
            current = self.read(kind)
            if expected_version != current['version']:
                raise PromptConflict('다른 변경이 먼저 저장됐습니다. 편집 내용을 보존한 뒤 최신 버전을 다시 불러오세요.')
            if text == current['yaml']:
                return current
            record = self._record(kind, text, note or '대시보드에서 수정')
            self._write(self._path(kind), text)
            history = list(reversed(current['history'])) + [record]
            self._write(self.history/(kind + '.json'), json.dumps(history, ensure_ascii=False, indent=2))
            return self.read(kind)

    def compare(self, kind, version):
        current = self.read(kind)
        previous = next((r for r in current['history'] if r['version'] == version), None)
        if previous is None:
            raise ValueError('저장된 버전을 찾지 못했습니다.')
        before, after = previous['yaml'].splitlines(keepends=True), current['yaml'].splitlines(keepends=True)
        rows, added, removed = [], 0, 0
        for tag, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag != 'equal':
                removed += b-a
                added += d-c
            for offset in range(max(b-a,d-c)):
                rows.append(dict(kind=tag,
                    old=dict(number=a+offset+1,text=before[a+offset]) if a+offset<b else None,
                    new=dict(number=c+offset+1,text=after[c+offset]) if c+offset<d else None))
        return dict(previous_version=version, current_version=current['version'], added=added, removed=removed, rows=rows)
