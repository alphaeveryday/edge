"""What a tool says about a result beyond its rows: the shape of a large set, rows as a table, and amounts as written."""
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

CATEGORIES = ('event_type', 'stage', 'role', 'kind', 'object_type', 'document_type', 'evidence_type', 'metric', 'metricCode',
              'factor', 'investor', 'series', 'name', 'ticker', 'companyName', 'securityName', 'seriesId', 'fiscalYear',
              'periodType', 'fsBasis', 'segmentName', 'eventType', 'lifecycleStage', 'currentStage')
DATES = ('reported_date', 'event_date', 'date', 'trade_date', 'as_of_date', 'published_at', 'available_at', 'period', 'observation_date',
         'tradeDate', 'observationDate', 'holdingsDate', 'eventDate', 'publishedAt', 'availableAt', 'snapshotDate')


def won_text(value):
    """A won amount as it is written, exact to the won: 2004376374000 -> '2조 43억 7,637만 4,000원'.

    Answers moved raw won into 억 and 조 by hand and landed a digit off (14.47억 written as 145억,
    2조 44억 as 2조 437억). The tool converts once so the answer can copy it.
    """
    if value is None:
        return None
    won = int(Decimal(str(value)).to_integral_value(ROUND_HALF_UP))
    # 조 is the largest unit written, so it carries everything above it: 12,345조, not 1경 2,345조.
    counts = [(abs(won) // 10**12, '조')] + [(abs(won) // unit % 10**4, name) for unit, name in ((10**8, '억'), (10**4, '만'), (1, ''))]
    parts = [f'{count:,}{name}' for count, name in counts if count]
    return ('-' if won < 0 else '') + (' '.join(parts) or '0') + '원'


def overview(rows):
    """Counts over every row of a result that does not fit on one page; never a sample of the first page."""
    counts = {}

    def add(key, value):
        if isinstance(value, str) and 0 < len(value) <= 60:
            counts.setdefault(key, Counter())[value] += 1

    for row in rows:
        if not isinstance(row, dict):
            continue
        flat = dict(row)
        # Observation rows keep their fields one or two levels down, and a linked row the object it leads to.
        for nested in ('observation', 'object', 'target', 'properties'):
            if isinstance(flat.get(nested), dict):
                flat = {**flat[nested], **flat}
        if isinstance(flat.get('properties'), dict):
            flat = {**flat['properties'], **flat}
        if 'event_ref' in row and isinstance(row.get('title'), str):   # event titles lead with the type code
            flat.setdefault('event_type', row['title'].split(' / ')[0])
        for key in CATEGORIES:
            add(key, str(flat[key]) if isinstance(flat.get(key), int) else flat.get(key))
        for person in row.get('participants') or []:
            if isinstance(person, dict):
                add('participant', person.get('name'))
        for key in DATES:
            value = flat.get(key)
            if isinstance(value, str) and len(value) >= 7:
                add(key + '_by_month', value[:7])
    return {key: [{'value': value, 'rows': n} for value, n in counter.most_common(15)]
                 + ([{'value': '(other values)', 'rows': sum(counter.values()) - sum(n for _, n in counter.most_common(15))}]
                    if len(counter) > 15 else [])
            for key, counter in counts.items() if len(counter) > 1 or key.endswith('_by_month')}


# A tool response over about 25,000 tokens never reaches the agent: the SDK replaces it with an error and a file
# path. The smallest response lost that way was 53,400 characters, so rows stop well before that.
BUDGET = 42000
# The title again, the id again, the raw source record, and ingestion bookkeeping no answer uses.
NOISE = ('displayTitle', 'id', 'calculationInputs', 'rceptNo', 'receivedAt', 'availabilityBasis', 'corpCode', 'instrumentCode', 'instrumentId', 'etfInstrumentId',
         'evidenceRunIds',                    # run ids of an earlier analysis: 2,800 of a report's 3,700 characters, and nothing resolves them
         'key_0', 'target_id', 'issuerId',    # link bookkeeping
         'input_ids')                         # the rows a total was summed from: they stay in the stored result


def fit(rows, limit, used=0):
    """How many of the first `limit` rows fit one response that already holds `used` characters; at least one."""
    import json
    size = used
    for count, row in enumerate(rows[:limit]):
        size += len(json.dumps(row, ensure_ascii=False)) + 2
        if size > BUDGET and count:
            return count
    return min(limit, len(rows))


def table(rows, keep_id=False, keep_title=False):
    """Observation rows as one header and value rows, with what every row shares said once.

    Each row used to be the whole graph object: its title twice, its id twice and the raw source record, 580 to
    1,550 characters. A hundred rows passed the response size the agent can receive, so it received none.
    """
    flat = []
    for row in rows:
        # A graph object keeps its values under properties; a calculated row is its values.
        values = {k: v for k, v in row.get('properties', row).items() if k not in NOISE and not isinstance(v, (dict, list))}
        values.update(row.get('money_text') or {})   # the written amount stands in for raw won
        values.update({k: v for k, v in row.items()
                       if k not in ('object_type', 'object_id', 'title', 'properties', 'money_text') and not isinstance(v, (dict, list))})
        if values.get('value_text') is not None:
            values.pop('value', None)   # the written amount stands in for raw won
        if keep_title and row.get('title') not in values.values():
            values['title'] = row.get('title')   # an event or a document is named only by its title
        if keep_id:
            values['object_id'] = row['object_id']
        flat.append({**({'object_type': row['object_type']} if 'object_type' in row else {}), **values})
    keys = [k for k in dict.fromkeys(k for values in flat for k in values) if any(v.get(k) is not None for v in flat)]
    keys.sort(key=lambda k: 0 if k in CATEGORIES else 1 if k in DATES else 2)   # who and when lead, then the values
    same = {k: flat[0].get(k) for k in keys if len(flat) > 1 and all(v.get(k) == flat[0].get(k) for v in flat)}
    columns = [k for k in keys if k not in same]
    return columns, [[values.get(k) for k in columns] for values in flat], same
