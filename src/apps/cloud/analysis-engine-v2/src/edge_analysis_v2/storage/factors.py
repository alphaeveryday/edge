"""Store factor detail cards and assemble screens from one outlook publication."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row


METRICS = {
    '차트': ('ma20_distance_pct', 'ma60_direction', 'new_closing_high_count_20d',
           'distance_from_52w_closing_high_pct', 'turnover_ratio_previous_day', 'atr14_pct'),
    '매크로': ('usd_krw', 'commodity_return_20d_pct', 'kr_treasury_10y_yield',
            'us_treasury_10y_yield', 'brent_spot_usd', 'days_until_policy_decision'),
    '밸류': ('weighted_per', 'weighted_pbr', 'weighted_per_band_5y_pct', 'distribution_yield_12m_pct'),
    '수급': ('weighted_institution_net_amount_20d', 'weighted_foreign_net_amount_20d',
           'weighted_institution_net_buy_streak', 'weighted_foreign_net_buy_streak', 'etf_units_change_20d_pct'),
}
HEADLINES = dict(zip(('강력상승', '상승', '중립', '하락', '강력하락'),
                     ('강한 상승 쪽이에요', '상승 쪽이에요', '중립 쪽이에요', '하락 쪽이에요', '강한 하락 쪽이에요')))


def _references(value):
    if not isinstance(value, list) or not value or any(type(v) is not str or not v for v in value):
        raise ValueError('Nonempty tool_run_ids required')
    if len(set(value)) != len(value):
        raise ValueError('Duplicate evidence')
    return value


def prepare_metrics(metrics: dict) -> list[dict]:
    """Validate card values while preserving date versus timestamp precision.

    Args:
        metrics: Factor names mapped to calculated card lists.

    Returns:
        Database rows ordered by the screen contract.
    """
    rows = []
    for factor, cards in metrics.items():
        if factor not in METRICS or not isinstance(cards, list):
            raise ValueError('Unknown factor or invalid cards')
        seen = set()
        for card in cards:
            key, value = card['key'], card['value']
            if key not in METRICS[factor] or key in seen:
                raise ValueError('Unknown or duplicate metric')
            seen.add(key)
            number, text = None, None
            if key == 'ma60_direction':
                if value not in ('상승', '횡보', '하락'):
                    raise ValueError('Invalid direction')
                text = value
            else:
                if type(value) not in (int, float, Decimal):
                    raise ValueError('Numeric metric required')
                number = Decimal(str(value))
                if not number.is_finite():
                    raise ValueError('Finite metric required')
                if key in ('new_closing_high_count_20d', 'days_until_policy_decision',
                           'weighted_institution_net_buy_streak', 'weighted_foreign_net_buy_streak'):
                    if number < 0 or number != number.to_integral_value():
                        raise ValueError('Nonnegative integer required')
                if key == 'new_closing_high_count_20d' and number > 20:
                    raise ValueError('High count exceeds 20 days')
            stamp = card['observed_at']
            day, at = None, None
            if len(stamp) == 10:
                day = date.fromisoformat(stamp)
            else:
                at = datetime.fromisoformat(stamp)
                if at.utcoffset() is None:
                    raise ValueError('Observation timestamp requires UTC offset')
            subject = card.get('subject')
            if subject is not None and (type(subject) is not str or not subject.strip()):
                raise ValueError('Invalid subject')
            rows.append(dict(factor_type=factor, metric_key=key, numeric_value=number,
                             text_value=text, observed_date=day, observed_at=at, subject=subject,
                             position=METRICS[factor].index(key), tool_run_ids=_references(card['tool_run_ids'])))
    return sorted(rows, key=lambda r: (list(METRICS).index(r['factor_type']), r['position']))


FACTOR_KEYS = {'차트': 'chart', '매크로': 'macro', '밸류': 'valuation', '수급': 'flow'}


def project_factor_metrics(output: dict, etf_code: str) -> dict:
    """Assemble screen cards from the unchanged instrument-tool response.

    Args:
        output: Stored response including its successful tool run ID.
        etf_code: The publication ETF, never an arbitrary queried constituent.

    Returns:
        Existing screen-card lists for the factors present in the response.

    Raises:
        ValueError: The response belongs to another instrument or aggregation.
    """
    result = output['result']
    if result.get('instrument_id') != etf_code:
        raise ValueError('Factor instrument must match the publication ETF')
    projected = {}
    for factor, key in FACTOR_KEYS.items():
        if key not in result:
            continue
        values = result[key]
        projected[factor] = cards = []
        if values is None:
            continue
        if key in ('flow', 'valuation') and values.get('scope') != 'holdings_weighted':
            raise ValueError('ETF cards require complete holdings-weighted observations')
        for metric in METRICS[factor]:
            value = values.get(metric)
            if value is None:
                continue
            if metric == 'weighted_per' and values.get('weighted_per_approximate'):
                # A card carries no derivation note; a PER built on an approximated Q4 EPS (FY−9M, ALPHA-1130)
                # is withheld from cards. It stays readable in the screen response, which says approximate.
                continue
            stamp = values.get('observed_at')
            subject = None
            if key == 'chart':
                if metric in ('new_closing_high_count_20d', 'turnover_ratio_previous_day', 'atr14_pct'):
                    stamp = values['finalized_observed_at']
                if metric == 'ma60_direction':
                    value = {'rising': '상승', 'flat': '횡보', 'falling': '하락'}[value]
            elif key == 'macro':
                stamp = values['metric_observed_at'][metric]
                subject = values.get('metric_subjects', {}).get(metric)
            elif metric == 'distribution_yield_12m_pct':
                stamp = values['distribution_observed_at']
            elif metric == 'etf_units_change_20d_pct':
                stamp = values['units_observed_at']
            card = dict(key=metric, value=value, observed_at=stamp, tool_run_ids=[output['tool_run_id']])
            if subject is not None:
                card['subject'] = subject
            cards.append(card)
    return projected


def _matches_calculation(row, run, etf_code):
    """Require the requested ETF, factor, value and time to match the saved run."""
    if (run['function_name'] != 'get_instrument_factors'
            or run['arguments'].get('instrument_id') != etf_code
            or FACTOR_KEYS[row['factor_type']] not in run['arguments'].get('factors', list(FACTOR_KEYS.values()))):
        return False
    output = run['output']
    if not isinstance(output, dict) or output.get('tool_run_id') != run['tool_run_id']:
        return False
    try:
        calculated = prepare_metrics(project_factor_metrics(output, etf_code))
    except (ValueError, TypeError, KeyError):
        return False
    fields = ('factor_type', 'metric_key', 'numeric_value', 'text_value', 'observed_date', 'observed_at', 'subject')
    return any(all(card[field] == row[field] for field in fields) for card in calculated)


def save_factor_details(connection, analysis_id: str, metrics: dict, issue: dict) -> None:
    """Replace details atomically inside the caller's publication transaction.

    Args:
        connection: PostgreSQL connection owned by the publication writer.
        analysis_id: Running outlook publication identifier.
        metrics: Calculated cards grouped by factor.
        issue: Agent headline and issue item list.
    """
    rows = prepare_metrics(metrics)
    if type(issue.get('headline')) is not str or not issue['headline'].strip():
        raise ValueError('Issue headline required')
    items = issue['items']
    if not isinstance(items, list):
        raise ValueError('Issue items must be a list')
    for item in items:
        if item['sentiment'] not in ('positive', 'neutral', 'negative'):
            raise ValueError('Invalid sentiment')
        if any(type(item[k]) is not str or not item[k].strip() for k in ('title_keyword', 'sentence')):
            raise ValueError('Issue text required')
        _references(item['tool_run_ids'])
    references = {r for item in rows + items for r in item['tool_run_ids']}
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT status, analysis_at, etf_code FROM outlook_analyses WHERE analysis_id=%s FOR UPDATE', (analysis_id,))
        parent = cur.fetchone()
        if not parent or parent['status'] != 'running':
            raise ValueError('Running outlook required')
        for row in rows:
            if ((row['observed_at'] and row['observed_at'] > parent['analysis_at']) or
                    (row['observed_date'] and row['observed_date'] > parent['analysis_at'].astimezone(ZoneInfo('Asia/Seoul')).date())):
                raise ValueError('Future observation')
        cur.execute('''SELECT r.tool_run_id, d.function_name, r.arguments, r.output
            FROM tool_runs r JOIN tool_definitions d USING (tool_id)
            WHERE r.tool_run_id=ANY(%s) AND r.outlook_analysis_id=%s AND r.status='completed' ''',
                    (list(references), analysis_id))
        evidence = {r['tool_run_id']: r for r in cur.fetchall()}
        if set(evidence) != references:
            raise ValueError('Evidence must belong to this successful analysis execution')
        for run in evidence.values():
            if run['function_name'] == 'get_issue_evidence' and run['arguments'].get('include_body') is not False:
                raise ValueError('Final news evidence must exclude body')
        for row in rows:
            if not any(_matches_calculation(row, evidence[identity], parent['etf_code']) for identity in row['tool_run_ids']):
                raise ValueError('Metric must match its final factor calculation')
        cur.execute('DELETE FROM outlook_factor_metrics WHERE analysis_id=%s', (analysis_id,))
        cur.execute('DELETE FROM outlook_issue_items WHERE analysis_id=%s', (analysis_id,))
        for row in rows:
            cur.execute('''INSERT INTO outlook_factor_metrics
                (analysis_id,factor_type,metric_key,numeric_value,text_value,observed_date,observed_at,subject,position,tool_run_ids)
                VALUES (%(analysis_id)s,%(factor_type)s,%(metric_key)s,%(numeric_value)s,%(text_value)s,
                        %(observed_date)s,%(observed_at)s,%(subject)s,%(position)s,%(tool_run_ids)s)''',
                        dict(row, analysis_id=analysis_id))
        for position, item in enumerate(items):
            cur.execute('''INSERT INTO outlook_issue_items
                (analysis_id,position,title_keyword,sentence,sentiment,tool_run_ids)
                VALUES (%s,%s,%s,%s,%s,%s)''', (analysis_id, position, item['title_keyword'],
                    item['sentence'], item['sentiment'], item['tool_run_ids']))
        cur.execute('UPDATE outlook_analyses SET issue_headline=%s WHERE analysis_id=%s',
                    (issue['headline'], analysis_id))


def read_factor_details(connection, analysis_id: str) -> dict:
    """Assemble all five detail screens from stored rows of one publication."""
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT analysis_at,issue_headline FROM outlook_analyses WHERE analysis_id=%s', (analysis_id,))
        parent = cur.fetchone()
        if not parent:
            raise ValueError('Unknown outlook')
        cur.execute('SELECT type,sticker FROM outlook_factors WHERE analysis_id=%s', (analysis_id,))
        factors = {r['type']: r['sticker'] for r in cur.fetchall()}
        if set(factors) != set(METRICS) | {'이슈'} or parent['issue_headline'] is None:
            raise ValueError('Incomplete factor details')
        screens = {f: dict(type=f, sticker=factors[f], headline=HEADLINES[factors[f]],
                           analysis_at=parent['analysis_at'].isoformat(), metrics=[]) for f in METRICS}
        cur.execute('SELECT * FROM outlook_factor_metrics WHERE analysis_id=%s ORDER BY factor_type,position', (analysis_id,))
        for row in cur.fetchall():
            number = row['numeric_value']
            value = row['text_value'] if number is None else int(number) if number == number.to_integral_value() else float(number)
            metric = dict(key=row['metric_key'], value=value,
                          observed_at=(row['observed_at'] or row['observed_date']).isoformat())
            if row['subject'] is not None:
                metric['subject'] = row['subject']
            screens[row['factor_type']]['metrics'].append(metric)
        cur.execute('''SELECT title_keyword,sentence,sentiment,tool_run_ids FROM outlook_issue_items
            WHERE analysis_id=%s ORDER BY position''', (analysis_id,))
        screens['이슈'] = dict(type='이슈', sticker=factors['이슈'], headline=parent['issue_headline'], items=cur.fetchall())
        return screens
