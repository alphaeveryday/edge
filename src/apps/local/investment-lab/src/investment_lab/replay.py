"""Immutable run inputs, one atomic ledger transaction per simulated trading day."""
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .accounting import Decision, Portfolio, execute, metrics, number
from .pit import Gate, instant


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,allow_nan=False)


def fingerprint(value):
    return sha256(encode(value).encode()).hexdigest()


def code_version():
    base = Path(__file__).parent
    files={str(p.relative_to(base)):sha256(p.read_bytes()).hexdigest()
           for p in sorted(base.rglob('*.py'))}
    lock=base.parents[1]/'uv.lock'
    files['dependency_lock']=sha256(lock.read_bytes()).hexdigest() if lock.exists() else None
    return fingerprint(files)


def validate(config, data):
    if not isinstance(config,dict) or not isinstance(data,dict):
        raise ValueError('Configuration and dataset objects required')
    for field in ('calendar','bars','distributions','records'):
        if not isinstance(data.get(field),list):
            raise ValueError('Dataset arrays required: '+field)
    days = data['calendar']
    for day in days:
        date.fromisoformat(day)
    if not days or days != sorted(set(days)) or not data.get('calendar_source'):
        raise ValueError('Explicit ordered trading calendar with source required')
    start,end = date.fromisoformat(config['start']),date.fromisoformat(config['end'])
    if start > end or config['start'] not in days or config['end'] not in days:
        raise ValueError('Period must use calendar session dates')
    if data['ticker'] != '305720' or data['price_basis'] != 'unadjusted' or data['distribution_coverage'] != 'complete':
        raise ValueError('305720 unadjusted prices and complete distributions required')
    if data.get('kind') not in ('test_fixture','historical'):
        raise ValueError('Explicit dataset kind required')
    if data['kind'] == 'historical' and not data.get('evidence'):
        raise ValueError('Historical dataset needs provenance evidence')
    if number(config['initial_cash']) <= 0 or config.get('split') not in ('development','evaluation'):
        raise ValueError('Positive initial cash and explicit research split required')
    for key in ('fee_bps','slippage_bps'):
        if not 0 <= number(config[key]) < 10000:
            raise ValueError('Explicit cost assumptions required')
    if type(config.get('price_lookback')) is not int or not 1 <= config['price_lookback'] <= 500:
        raise ValueError('Price lookback must be 1..500')
    if not config.get('model') or not config.get('instructions'):
        raise ValueError('Frozen model and instructions required')
    bars = {bar['date']:bar for bar in data['bars']}
    if len(bars) != len(data['bars']) or any(d not in bars for d in days if config['start'] <= d <= config['end']):
        raise ValueError('Duplicate or missing calendar session price')
    for bar in data['bars']:
        if number(bar['open']) <= 0 or number(bar['close']) <= 0 or type(bar['suspended']) is not bool:
            raise ValueError('Positive opening/closing and explicit suspension flag required')
        instant(bar['available_at'])
    ids = set()
    for event in data['distributions']:
        if event['id'] in ids or number(event['amount']) < 0 or date.fromisoformat(event['ex_date']) > date.fromisoformat(event['pay_date']):
            raise ValueError('Invalid or duplicate distribution')
        ids.add(event['id'])


def create(root, config, data):
    validate(config,data)
    folder = Path(root)/uuid4().hex
    folder.mkdir(parents=True)
    for name,value in [('config',config),('dataset',data)]:
        (folder/f'{name}.json').write_text(encode(value),encoding='utf-8')
    manifest = dict(config_hash=fingerprint(config),data_hash=fingerprint(data),code_hash=code_version(),
                    dataset_kind=data['kind'],created_at=datetime.now(timezone.utc).isoformat(),status='not_started',error=None)
    with sqlite3.connect(folder/'ledger.sqlite') as conn:
        conn.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        conn.execute('CREATE TABLE days (date TEXT PRIMARY KEY, body TEXT NOT NULL)')
        conn.execute('INSERT INTO metadata VALUES (1,?)',(encode(manifest),))
    return folder


def _portfolio(value):
    return Portfolio(number(value['cash']),value['shares'],number(value['receivable']))


def _settle(portfolio, events, day, obligations):
    cash, receivable = portfolio.cash,portfolio.receivable
    for event in events:
        if event['ex_date'] == day:
            amount = portfolio.shares * number(event['amount'])
            obligations[event['id']] = dict(pay_date=event['pay_date'],amount=str(amount))
            receivable += amount
    paid = D('0')
    for key,item in list(obligations.items()):
        if item['pay_date'] <= day:
            paid += number(item['amount'])
            del obligations[key]
    return Portfolio(cash+paid,portfolio.shares,receivable-paid),paid


def resume(folder, decide, max_days=None):
    if max_days is not None and (type(max_days) is not int or not 1<=max_days<=520):
        raise ValueError('Daily execution budget must be 1..520')
    folder = Path(folder)
    config = json.loads((folder/'config.json').read_text(encoding='utf-8'))
    data = json.loads((folder/'dataset.json').read_text(encoding='utf-8'))
    with sqlite3.connect(folder/'ledger.sqlite',timeout=1) as conn:
        meta = json.loads(conn.execute('SELECT body FROM metadata').fetchone()[0])
        if any(meta[key] != value for key,value in [('config_hash',fingerprint(config)),('data_hash',fingerprint(data)),('code_hash',code_version())]):
            raise ValueError('Frozen run inputs or code changed; create a new experiment')
        days = [d for d in data['calendar'] if config['start'] <= d <= config['end']]
        bars = {b['date']:b for b in data['bars']}
        processed = 0
        for day in days:
            if max_days is not None and processed >= max_days:
                meta.update(status='paused',error=None)
                conn.execute('UPDATE metadata SET body=?',(encode(meta),))
                conn.commit()
                break
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM days WHERE date=?',(day,)).fetchone():
                conn.rollback()
                continue
            row = conn.execute('SELECT body FROM days ORDER BY date DESC LIMIT 1').fetchone()
            previous = json.loads(row[0]) if row else None
            bar,cutoff = bars[day],day+'T18:00:00+09:00'
            try:
                streams = {}
                for name in ('strategy','benchmark','gross'):
                    prev = previous['streams'][name] if previous else None
                    portfolio = _portfolio(prev['portfolio']) if prev else Portfolio(number(config['initial_cash']),0)
                    obligations = dict(prev['obligations']) if prev else {}
                    portfolio,paid = _settle(portfolio,data['distributions'],day,obligations)
                    choice = previous['decision'] if previous else dict(action='HOLD',weight=None)
                    if name == 'benchmark' and previous:
                        choice = dict(action='TARGET',weight='1') if portfolio.shares == 0 else dict(action='HOLD',weight=None)
                    action = Decision(choice['action'],number(choice['weight']) if choice.get('weight') is not None else None)
                    if bar['suspended']:
                        action = Decision('HOLD')
                    portfolio,trade = execute(portfolio,action,number(bar['open']),
                        number(config['fee_bps']) if name != 'gross' else D(0),
                        number(config['slippage_bps']) if name != 'gross' else D(0))
                    streams[name] = dict(portfolio=asdict(portfolio),obligations=obligations,
                        trade=trade,nav=str(portfolio.nav(bar['close'])),distribution_paid=str(paid))
                gate = Gate(cutoff,data['records'])
                prices = gate.price_history(day,config['price_lookback'])
                context = dict(date=day,cutoff=cutoff,ticker='305720',portfolio=streams['strategy']['portfolio'],
                    prices=prices,previous_decision=previous['decision'] if previous else None,
                    instructions=config['instructions'])
                result = decide(json.loads(encode(context)),gate)
                action = Decision(result['action'],number(result['weight']) if result.get('weight') is not None else None)
                for key in ('reason','uncertainty','change_condition'):
                    if not isinstance(result.get(key),str) or not result[key].strip():
                        raise ValueError('Decision explanation fields required')
                evidence = result.get('evidence_ids')
                allowed = {x['id'] for call in gate.calls for x in call['response']['items']}
                if not isinstance(evidence,list) or any(not isinstance(x,str) or x not in allowed for x in evidence):
                    raise ValueError('Evidence must reference admitted records')
                result = {key:result[key] for key in ('action','reason','uncertainty','change_condition','evidence_ids')}
                result['weight'] = str(action.weight) if action.weight is not None else None
                history = json.loads(previous['history_dates']) if previous else []
                current = dict(date=day,cutoff=cutoff,context=context,decision=result,gate_audit=gate.audit,tool_calls=gate.calls,
                    streams=streams,history_dates=encode(history+[day]),execution_status='cancelled_suspension' if bar['suspended'] else 'processed',
                    **streams['strategy'])
                conn.execute('INSERT INTO days VALUES (?,?)',(day,encode(current)))
                meta.update(status='completed' if day==days[-1] else 'running',error=None)
                conn.execute('UPDATE metadata SET body=?',(encode(meta),))
                conn.commit()
                processed += 1
            except Exception as exc:
                conn.rollback()
                # Exception messages can contain remote credentials; store only class.
                meta.update(status='failed',error=type(exc).__name__)
                conn.execute('UPDATE metadata SET body=?',(encode(meta),))
                conn.commit()
                raise


def read_run(folder):
    folder = Path(folder)
    config = json.loads((folder/'config.json').read_text(encoding='utf-8'))
    with sqlite3.connect(f'file:{(folder/"ledger.sqlite").as_posix()}?mode=ro',uri=True) as conn:
        meta = json.loads(conn.execute('SELECT body FROM metadata').fetchone()[0])
        days = [json.loads(r[0]) for r in conn.execute('SELECT body FROM days ORDER BY date')]
    reports = {}
    for name in ('strategy','benchmark','gross'):
        nav = [float(config['initial_cash'])]+[float(d['streams'][name]['nav']) for d in days]
        durations = [0]+[(date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days for a,b in zip(days,days[1:])] if days else []
        reports[name] = metrics(nav,[d['streams'][name]['trade']['notional'] for d in days],durations)
        reports[name]['costs'] = sum(float(d['streams'][name]['trade']['fee'])+float(d['streams'][name]['trade']['slippage']) for d in days)
        reports[name]['trades'] = sum(d['streams'][name]['trade']['quantity'] != 0 for d in days)
        if not days:
            reports[name].update({key:None for key in ('cumulative_return','annual_return','turnover','sharpe','mdd','costs','trades')})
    estimates=[]
    usage=[]
    for event_file in sorted((folder/'agent').glob('*/events.jsonl')):
        for line in event_file.read_text(encoding='utf-8').splitlines():
            try:
                event=json.loads(line)
                if event.get('message_type')=='ResultMessage':
                    message=event['message']
                    estimates.append(message.get('total_cost_usd'))
                    usage.append(message.get('usage'))
            except (ValueError,KeyError):
                estimates.append(None)
    complete_estimate=estimates and all(isinstance(x,(int,float)) and number(x)>=0 for x in estimates)
    return dict(id=folder.name,config=config,metadata=meta,days=days,metrics=reports,
                model_cost=sum(estimates) if complete_estimate else None,model_usage=usage,
                model_cost_note='SDK estimate, not verified billing; null if any completed call lacks cost',
                pending_order_status='unexecuted_end_of_period' if meta['status']=='completed' else 'pending_next_session')
