"""Executable final-screen contracts, independent of model and DB assembly schemas."""
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match
from edge_analysis_v2.contracts.check_catalog import describe_check, schema_checks

CONTRACTS = Path(__file__).parent
KST = ZoneInfo('Asia/Seoul')
FACTOR_VIEWS = {'issue': '이슈', 'chart': '차트', 'macro': '매크로', 'valuation': '밸류', 'flow': '수급'}
FORMATS = FormatChecker()


@FORMATS.checks('date', raises=ValueError)
def _date(value):
    return not isinstance(value, str) or bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) and date.fromisoformat(value))


@FORMATS.checks('date-time', raises=ValueError)
def _timestamp(value):
    # jsonschema's optional RFC3339 dependency must not turn validation into a no-op.
    return not isinstance(value, str) or bool(
        re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value)
        and datetime.fromisoformat(value).utcoffset() is not None)


def contract_info():
    """Return the reviewed document version and immutable source fingerprints."""
    info = json.loads((CONTRACTS / 'manifest.json').read_text(encoding='utf-8'))
    info['directory'] = str(CONTRACTS)
    info['schema_sha256'] = sha256((CONTRACTS / 'screen-output.schema.json').read_bytes()).hexdigest()
    return info


def source_status(vault):
    """Compare the current Obsidian documents with reviewed snapshots, without writing."""
    sources = []
    for source in contract_info()['sources']:
        state, digest = 'not_configured', None
        if vault is not None:
            try:
                digest = sha256((Path(vault) / source['vault_path']).read_text(encoding='utf-8').encode('utf-8')).hexdigest()
                state = 'matched' if digest == source['sha256'] else 'changed'
            except OSError:
                state = 'unavailable'
        sources.append(dict(source, status=state, actual_sha256=digest))
    states = {source['status'] for source in sources}
    status = ('changed' if 'changed' in states else
              'matched' if states == {'matched'} else
              'not_configured' if states == {'not_configured'} else 'unavailable')
    return dict(status=status, sources=sources)


def schema_for(view):
    """Expand local definitions for a self-contained schema readable beside the response."""
    bundle = json.loads((CONTRACTS / 'screen-output.schema.json').read_text(encoding='utf-8'))
    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if '$ref' in value:
                return expand(bundle['$defs'][value['$ref'].removeprefix('#/$defs/')])
            return {key: expand(item) for key, item in value.items()}
        return value
    return {'$schema': bundle['$schema'], 'title': view, **expand(bundle['$defs'][view])}


def pointer(parts):
    """Encode a location using JSON Pointer, including unusual user-controlled keys."""
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in parts) if parts else ''


def _schema_errors(schema, payload):
    errors = []
    def relevant(error):
        # Metrics use a keyed union. Report the selected card's missing field, not
        # the unrelated errors from every other metric variant.
        if error.validator == 'oneOf' and isinstance(error.instance, dict) and 'key' in error.instance:
            for i, variant in enumerate(error.validator_value):
                if variant.get('properties', {}).get('key', {}).get('const') == error.instance['key']:
                    return [leaf for child in error.context if child.schema_path[0] == i for leaf in relevant(child)]
        return [best_match([error]) if error.validator in ('oneOf', 'anyOf') else error]
    failures = Draft202012Validator(schema, format_checker=FORMATS).iter_errors(payload)
    for error in (leaf for failure in failures for leaf in relevant(failure)):
        paths = [list(error.absolute_path)]
        if error.validator == 'required' and isinstance(error.instance, dict):
            paths = [paths[0] + [key] for key in error.validator_value if key not in error.instance]
        elif error.validator == 'additionalProperties' and isinstance(error.instance, dict):
            paths = [paths[0] + [key] for key in error.instance if key not in error.schema.get('properties', {})]
        errors.extend(dict(path=pointer(path), schema_path=pointer(error.absolute_schema_path),
                           rule=error.validator, message=error.message, expected=error.validator_value) for path in paths)
    def finite(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(dict(path=pointer(path), schema_path='', rule='finite', message='JSON numbers must be finite'))
        elif isinstance(value, dict):
            for key, item in value.items():
                finite(item, path + [key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                finite(item, path + [index])
    finite(payload, [])
    return sorted(errors, key=lambda e: (e['path'], e['rule']))


def audit_payload(view, payload, *, parent=None, context=None):
    """Validate the exact assembled value; never repair it to make a contract pass."""
    schema = schema_for(view)
    errors = _schema_errors(schema, payload)
    checks = []
    def check(identity, title, issues=(), *, unavailable=False):
        checks.append(dict(id=identity, title=title,
                           status='not_checked' if unavailable else 'failed' if issues else 'passed',
                           issues=list(issues)))
    def issue(path, expected, actual):
        return dict(path=path, expected=expected, actual=actual)
    if errors:
        check('semantic-precondition', '구조 오류 때문에 의미 규칙 검사를 수행하지 못함', unavailable=True)
    elif view == 'movement':
        items = payload['items']
        valid = (payload['summary'] is not None) == bool(items)
        check('movement-empty', '선정 항목이 없을 때만 요약이 null', [] if valid else [issue('/summary', '항목 유무와 일치', payload['summary'])])
        ids = [item['item_id'] for item in items]
        check('movement-identities', '선정된 항목 ID 중복 없음', [] if len(ids) == len(set(ids)) else [issue('/items', '고유 item_id', ids)])
        cutoff = datetime.fromisoformat(payload['publication']['analysis_at'])
        check('movement-cutoff', '항목 자료 상한이 분석 기준시각 이후가 아님', [
            issue(f'/items/{i}/source_as_of', '<= analysis_at', item['source_as_of'])
            for i, item in enumerate(items) if datetime.fromisoformat(item['source_as_of']) > cutoff])
    elif view == 'outlook':
        detail, publication = payload['detail'], payload['publication']
        date = datetime.fromisoformat(publication['analysis_at']).astimezone(KST).date().isoformat()
        check('update-date', '업데이트 날짜가 분석 기준시각의 KST 날짜', [] if detail['updates']['date'] == date else [issue('/detail/updates/date', date, detail['updates']['date'])])
        topics = detail['items']
        ids = [item['id'] for item in topics]
        updates = detail['updates']['items']
        update_ids = [item['id'] for item in updates]
        duplicates = [issue('/detail/items', '고유 논점 ID', ids)] if len(ids) != len(set(ids)) else []
        if len(update_ids) != len(set(update_ids)):
            duplicates.append(issue('/detail/updates/items', '논점당 하루 한 기록', update_ids))
        check('topic-identities', '본문·업데이트 논점 ID 중복 없음', duplicates)
        known = {item['id']: item for item in topics}
        links = []
        order = []
        for i, update in enumerate(updates):
            path = f'/detail/updates/items/{i}'
            if update['change_type'] == 'deleted':
                order.append(len(ids))
                if update['id'] in known or update['sentence'] is not None:
                    links.append(issue(path, '본문에서 제외 + sentence=null', update))
            elif update['id'] not in known:
                links.append(issue(path + '/id', '현재 본문 논점 ID', update['id']))
            else:
                order.append(ids.index(update['id']))
                for field in ('title_keyword', 'tool_run_ids'):
                    if update[field] != known[update['id']][field]:
                        links.append(issue(path + '/' + field, known[update['id']][field], update[field]))
        if order != sorted(order):
            links.append(issue('/detail/updates/items', '현재 본문 순서, 삭제 기록은 뒤', update_ids))
        check('update-links', '업데이트의 제목·근거·삭제 상태와 표시 순서', links)
        first = context is not None and 'previous_analysis_id' in context and context['previous_analysis_id'] is None
        initial = []
        if first:
            if updates:
                initial.append(issue('/detail/updates/items', [], updates))
            initial.extend(issue(f'/detail/items/{i}/sentences/{j}/is_updated', False, True)
                           for i, topic in enumerate(topics) for j, sentence in enumerate(topic['sentences']) if sentence['is_updated'])
        check('initial-body', '최초 발행은 강조·변경 기록 없음', initial,
              unavailable=context is None or 'previous_analysis_id' not in context)
    elif view in FACTOR_VIEWS:
        if parent is None or _schema_errors(schema_for('outlook'), parent):
            check('parent-factor', '같은 발행본의 부모 요인 스티커', unavailable=True)
        else:
            factor = next(item for item in parent['factors'] if item['type'] == FACTOR_VIEWS[view])
            check('parent-factor', '같은 발행본의 부모 요인 스티커', [] if payload['sticker'] == factor['sticker'] else [issue('/sticker', factor['sticker'], payload['sticker'])])
        if view != 'issue':
            if parent is None or 'publication' not in parent:
                check('parent-time', '부모 분석 기준시각 유지', unavailable=True)
            else:
                expected = parent['publication']['analysis_at']
                check('parent-time', '부모 분석 기준시각 유지', [] if datetime.fromisoformat(payload['analysis_at']) == datetime.fromisoformat(expected) else [issue('/analysis_at', expected, payload['analysis_at'])])
            order = schema['x-metric-order']
            keys = [item['key'] for item in payload['metrics']]
            valid = len(keys) == len(set(keys)) and keys == sorted(keys, key=order.index)
            check('metric-order', '가용 지표는 계약 순서로, 중복 없이 표시', [] if valid else [issue('/metrics', order, keys)])
            cutoff = datetime.fromisoformat(payload['analysis_at'])
            future = []
            for i, metric in enumerate(payload['metrics']):
                at = metric['observed_at']
                later = at > cutoff.astimezone(KST).date().isoformat() if len(at) == 10 else datetime.fromisoformat(at) > cutoff
                if later:
                    future.append(issue(f'/metrics/{i}/observed_at', '<= analysis_at', at))
            check('observation-cutoff', '관측 날짜·시각이 분석 기준 이후가 아님', future)
            if view == 'valuation':
                check('band-policy', '미정인 5년 PER 밴드는 계산·표시 보류', [] if 'weighted_per_band_5y_pct' not in keys else [issue('/metrics', '5년 밴드 제외', keys)])
    status = ('failed' if errors or any(c['status'] == 'failed' for c in checks) else
              'partial' if any(c['status'] == 'not_checked' for c in checks) else 'passed')
    return dict(view=view, status=status, schema=schema, data=deepcopy(payload), schema_errors=errors,
                schema_checks=schema_checks(schema, errors), checks=[describe_check(c) for c in checks])
