"""Read-only audits of the exact dashboard response, with inspectable evidence."""
from datetime import datetime, timezone
from copy import deepcopy
import html
import json

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from edge_analysis_v2.contracts.screen_validation import FACTOR_VIEWS, audit_payload, contract_info, schema_for, source_status, pointer
from edge_analysis_v2.contracts.check_catalog import describe_check

LABELS = {'movement': '오늘 움직임 · 전체', 'movement-summary': '오늘 움직임 · 요약',
          'movement-detail': '오늘 움직임 · 상세', 'outlook': '전망 · 전체',
          'outlook-summary': '전망 · 요약', 'outlook-detail': '전망 · 본문·업데이트',
          'outlook-factors': '전망 · 5요인', 'outlook-conclusion': '전망 · 결론',
          'factor-details': '5요인 상세 · 전체', 'issue': '이슈 상세', 'chart': '차트 상세',
          'macro': '매크로 상세', 'valuation': '밸류 상세', 'flow': '수급 상세'}
STATES = {'passed': '통과', 'failed': '실패', 'partial': '미검증 있음', 'not_checked': '미검증',
          'matched': '현재 옵시디언과 일치', 'changed': '계약 문서 변경 감지',
          'not_configured': '옵시디언 경로 미설정', 'unavailable': '옵시디언 문서 조회 불가'}


def unchecked_report(kind, identity, reason, *, vault=None):
    info = contract_info()
    info.pop('directory')
    return dict(status='not_checked', kind=kind, analysis_id=identity,
                audited_at=datetime.now(timezone.utc).isoformat(), contract=info,
                sources=source_status(vault), entries=[], origin=reason)


def read_contract_audit(connection, kind, identity, *, vault=None):
    """Audit completed rows in one read-only snapshot; no model calls or data repairs."""
    from edge_analysis_v2.dashboard.server import assemble_screen
    from edge_analysis_v2.storage.publications import PublicationStore
    if kind not in ('movement', 'outlook'):
        raise ValueError('Unknown analysis kind')
    if not connection.autocommit or connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Audit requires an idle autocommit connection')
    entries, responses = [], {}
    store = PublicationStore(connection)
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute(sql.SQL('SELECT previous_analysis_id,status FROM {} WHERE analysis_id=%s').format(
            sql.Identifier(kind + '_analyses')), (identity,))
        context = cur.fetchone()
        if context is None:
            return None
        if context['status'] != 'completed':
            return unchecked_report(kind, identity, '완료된 DB 발행본이 없어 검사하지 못했습니다. DB 상태: ' + context['status'], vault=vault)
        features = ['all', 'summary', 'detail'] if kind == 'movement' else ['all', 'summary', 'detail', 'factors', 'conclusion', 'factor_details']
        for feature in features:
            view = kind if feature == 'all' else 'factor-details' if feature == 'factor_details' else kind + '-' + feature
            try:
                value = assemble_screen(connection, kind, identity, feature, store=store)
            except (ValueError, KeyError, TypeError) as error:
                entries.append(dict(view=view, status='failed', schema=schema_for(view), data=None,
                    schema_errors=[dict(path='', schema_path='', rule='assembly',
                                        message='DB 조립 실패 (' + type(error).__name__ + ')')], checks=[]))
                continue
            responses[feature] = value
            entries.append(audit_payload(view, value, context=context))
        if kind == 'outlook' and isinstance(responses.get('factor_details'), dict):
            for view, factor in FACTOR_VIEWS.items():
                entries.append(audit_payload(view, responses['factor_details'].get(factor), parent=responses.get('all'), context=context))
        # A partial screen must not hide failed rules from the same full response.
        inherited = {'outlook-detail': {'update-date', 'topic-identities', 'update-links', 'initial-body'},
                     'movement-summary': {'movement-empty'},
                     'movement-detail': {'movement-identities', 'movement-cutoff'}}
        full = entries[0]
        for entry in entries[1:]:
            if entry['view'] == 'factor-details':
                children = [e for e in entries if e['view'] in FACTOR_VIEWS]
                issues = [dict(path='/' + FACTOR_VIEWS[e['view']], expected='개별 계약 통과', actual=e['status'])
                          for e in children if e['status'] != 'passed']
                state = 'failed' if any(e['status'] == 'failed' for e in children) else 'not_checked' if issues else 'passed'
                entry['checks'].append(dict(id='child-contracts', title='5요인 상세 개별 의미 규칙', status=state, issues=issues))
            if entry['view'].startswith(kind + '-') and isinstance(full['data'], dict) and isinstance(entry['data'], dict):
                entry['checks'].extend(deepcopy(c) for c in full['checks'] if c['id'] in inherited.get(entry['view'], set()))
                if entry['view'] in inherited:
                    entry['checks'].extend(deepcopy(c) for c in full['checks'] if c['id'] == 'semantic-precondition')
                issues = [dict(path='/' + key, expected=full['data'].get(key), actual=value)
                          for key, value in entry['data'].items() if value != full['data'].get(key)]
                entry['checks'].append(dict(id='projection-equality', title='전체 응답과 기능별 조회 결과 일치',
                                            status='failed' if issues else 'passed', issues=issues))
            if any(c['status'] == 'failed' for c in entry['checks']):
                entry['status'] = 'failed'
            elif entry['status'] == 'passed' and any(c['status'] == 'not_checked' for c in entry['checks']):
                entry['status'] = 'partial'
    info = contract_info()
    info.pop('directory')
    sources = source_status(vault)
    for entry in entries:
        entry['checks'] = [describe_check(c) for c in entry['checks']]
    status = ('failed' if sources['status'] == 'changed' or any(e['status'] == 'failed' for e in entries) else
              'partial' if sources['status'] != 'matched' or any(e['status'] == 'partial' for e in entries) else 'passed')
    return dict(status=status, kind=kind, analysis_id=identity, audited_at=datetime.now(timezone.utc).isoformat(),
                contract=info, sources=sources, entries=entries,
                origin='같은 읽기 전용 DB 스냅샷에서 실제 화면 조회 경로로 조립')


def render_audit_json(data, failure_paths):
    """Keep server JSON precision while marking failing values or missing-field parents."""
    targets = {}
    for path in failure_paths:
        value, resolved = data, []
        for token in path.split('/')[1:] if path else []:
            key = token.replace('~1', '/').replace('~0', '~')
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
                value = value[int(key)]
            else:
                break
            resolved.append(key)
        targets.setdefault(pointer(resolved), []).append(path or '/')
    def render(value, parts):
        depth = len(parts)
        if isinstance(value, (dict, list)) and value:
            mapping = isinstance(value, dict)
            children = value.items() if mapping else enumerate(value)
            body = ('{' if mapping else '[') + '\n' + ',\n'.join(
                '  ' * (depth + 1)
                + (html.escape(json.dumps(key, ensure_ascii=False)) + ': ' if mapping else '')
                + render(child, parts + [key]) for key, child in children
            ) + '\n' + '  ' * depth + ('}' if mapping else ']')
        else:
            body = html.escape(json.dumps(value, ensure_ascii=False))
        path = pointer(parts)
        if path in targets:
            body = ('<span class="audit-json-error" data-json-path="' + html.escape(path, quote=True)
                    + '" tabindex="-1" title="계약 위반: ' + html.escape(', '.join(targets[path]), quote=True)
                    + '">' + body + '</span>')
        return body
    return render(data, [])


def render_contract_audit(report):
    """Escape every persisted value; JSON is serialized on the server without JS rounding."""
    escape = lambda value: html.escape(str(value), quote=True)
    pretty = lambda value: escape(json.dumps(value, ensure_ascii=False, indent=2))
    badge = lambda state: '<span class="audit-state audit-' + escape(state) + '">' + escape(STATES.get(state, state)) + '</span>'
    schema_errors = sum(len(e['schema_errors']) for e in report['entries'])
    rule_errors = sum(c['status'] == 'failed' for e in report['entries'] for c in e['checks'])
    pending = sum(c['status'] == 'not_checked' for e in report['entries'] for c in e['checks'])
    contract = report['contract']
    if not report['entries']:
        return ('<section class="panel contract-audit"><h2>출력 계약 검사 · 미검증</h2><p>'
                + escape(report['origin']) + '</p><p>통과로 처리하지 않습니다. 에이전트 관측에서 진행 상태를 확인하세요.</p>'
                '<button type="button" data-audit-refresh>DB에서 다시 감사</button></section>')
    parts = ['<section class="panel contract-audit"><div class="toolbar"><h2>출력 계약 감사</h2>', badge(report['status']),
             '<button type="button" data-audit-refresh>DB에서 다시 감사</button></div>',
             '<p>구조 오류 <strong>' + str(schema_errors) + '</strong> · 의미 규칙 실패 <strong>' + str(rule_errors)
             + '</strong> · 규칙 미검증 ' + str(pending) + '</p>',
             '<p class="muted">구조 검사: 중첩 객체·배열의 계층, 필드 타입, 필수·추가 키, enum, 배열 개수·순서, 날짜 형식. 의미 규칙은 별도로 검사합니다.</p>',
             '<p class="muted">' + escape(report['origin']) + '<br>계약 ' + escape(contract['version'])
             + ' · 감사 시각 ' + escape(report['audited_at']) + '<br>분석 ID ' + escape(report['analysis_id']) + '</p>',
             '<details><summary>계약 근거 · ' + escape(STATES[report['sources']['status']]) + '</summary>',
             '<p>옵시디언 계약을 JSON Schema로 명시한 검토본입니다. 원문 해시는 문서 변경을 감지하며, 문장 의미의 동등성을 자동 증명하지 않습니다.</p>',
             '<p class="muted">스키마 SHA-256: ' + escape(contract['schema_sha256']) + '</p>']
    for source in report['sources']['sources']:
        parts.append('<p>' + escape(source['vault_path']) + ' · ' + escape(STATES.get(source['status'], source['status']))
                     + '<br><small>기준 SHA-256: ' + escape(source['sha256']) + '</small></p>')
    parts.extend(['</details><details><summary>이번 자동 감사 범위 밖 · ' + str(len(contract['not_checked'])) + '항목</summary><ul>',
                  *['<li>' + escape(item) + '</li>' for item in contract['not_checked']],
                  '</ul><p>자동 검사 통과는 문장 품질·투자 판단 또는 원천 계산 전체의 합격을 뜻하지 않습니다.</p></details>',
                  '<label for="audit-view">좌우 비교할 출력 유형</label><select id="audit-view">'])
    initial = next((i for i, e in enumerate(report['entries']) if e['status'] == 'failed'), 0)
    for i, entry in enumerate(report['entries']):
        parts.append('<option value="' + str(i) + '"' + (' selected' if i == initial else '') + '>'
                     + escape(LABELS[entry['view']] + ' · ' + STATES[entry['status']]) + '</option>')
    parts.append('</select></section>')
    failures = []
    for i, entry in enumerate(report['entries']):
        for error in entry['schema_errors']:
            failures.append((i, error['path'], error['message']))
        for check in entry['checks']:
            if check['status'] == 'failed':
                for issue in check['issues']:
                    failures.append((i, issue['path'], check['title'] + ' · 기대: '
                                     + json.dumps(issue['expected'], ensure_ascii=False) + ' · 실제: '
                                     + json.dumps(issue['actual'], ensure_ascii=False)))
    if failures:
        parts.append('<section class="panel audit-errors"><h3>실패 위치 · 클릭하여 해당 출력 비교</h3><ul>')
        for i, path, message in failures:
            parts.append('<li><button type="button" data-audit-jump="' + str(i) + '" data-audit-path="'
                         + escape(path) + '">' + escape(LABELS[report['entries'][i]['view']]) + ' · '
                         + escape(path or '/') + '</button><p>' + escape(message) + '</p></li>')
        parts.append('</ul></section>')
    for i, entry in enumerate(report['entries']):
        parts.append('<section data-audit-entry="' + str(i) + '"' + (' hidden' if i != initial else '') + '>')
        parts.append('<div class="panel"><h3>' + escape(LABELS[entry['view']]) + ' ' + badge(entry['status']) + '</h3>')
        parts.append('<h3>1. JSON 타입·구조 검사</h3>')
        if not entry.get('schema_checks'):
            parts.append('<p class="audit-not_checked">세부 구조 검사 기록 없음 · DB 조립 실패 여부를 확인하고 다시 검사하세요.</p>')
        for check in entry.get('schema_checks', []):
            parts.append('<div class="audit-rule" data-schema-check="' + escape(check['id']) + '">'
                         + badge(check['status']) + ' ' + escape(check['title'])
                         + '<p>' + escape(check['description']) + '<br>검사 ID: ' + escape(check['id'])
                         + '<br>정의: ' + escape(check['source']) + '</p>')
            for error in check['errors']:
                parts.append('<p><code>' + escape(error['path'] or '/') + '</code> · ' + escape(error['message']) + '</p>')
            parts.append('</div>')
        if entry['schema_errors']:
            parts.append('<p class="muted">오류가 없는 세부 항목도 전체 구조 오류가 해결되기 전에는 보수적으로 미검증으로 표시합니다.</p>')
        if entry['schema_errors']:
            parts.append('<ul class="audit-errors">')
            for error in entry['schema_errors']:
                parts.append('<li><code>' + escape(error['path'] or '/') + '</code> · ' + escape(error['rule'])
                             + '<p>' + escape(error['message']) + '</p></li>')
            parts.append('</ul>')
        else:
            parts.append('<p class="audit-passed">JSON Schema 구조 검사 통과</p>')
        parts.append('<h3>2. 계약 규칙·조립 일관성 검사</h3>')
        for check in entry['checks']:
            parts.append('<div class="audit-rule">' + badge(check['status']) + ' ' + escape(check['title']))
            if (check['id'] == 'initial-body' and check['status'] == 'failed' and check['issues']
                    and all(issue['path'].endswith('/is_updated') for issue in check['issues'])):
                parts.append('<p class="source-note">화면 강조 표시 오류: 최초 발행인데 변경 강조가 켜져 있습니다. '
                             '이 오류는 본문 문장이나 수치의 오류를 뜻하지 않습니다. 계약 실패는 유지하며 저장값을 자동 수정하지 않습니다. '
                             '초안 작성 중 수정과 이전 발행본 대비 변경을 구분해야 합니다.</p>')
            parts.append('<p class="muted">검사 ID: ' + escape(check['id']) + '<br>'
                         + ('계약 근거: ' if check.get('category') == 'contract' else '구현 검증 근거: ')
                         + escape(check.get('source', '기록 없음')) + '</p>')
            for issue in check['issues']:
                parts.append('<p><code>' + escape(issue['path'] or '/') + '</code><br>기대: '
                             + pretty(issue['expected']) + '<br>실제: ' + pretty(issue['actual']) + '</p>')
            parts.append('</div>')
        parts.append('</div><div class="audit-columns"><section class="panel"><h3>계약 JSON Schema</h3>'
                     '<p class="muted">required · type · enum · additionalProperties</p><pre class="audit-json">'
                     + pretty(entry['schema']) + '</pre></section><section class="panel"><h3>DB에서 조립된 실제 JSON</h3>'
                     '<p class="muted">빨간색 = 계약 위반 값 · 필드 누락은 상위 객체 표시 · 자동 수정 없음</p><pre class="audit-json" data-audit-json>'
                     + render_audit_json(entry['data'], [path for index, path, _ in failures if index == i])
                     + '</pre></section></div></section>')
    return ''.join(parts)
