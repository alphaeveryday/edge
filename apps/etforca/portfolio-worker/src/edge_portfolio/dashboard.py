"""One editable workbook per visitor, independent of analysis and email records."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from typing import Annotated, Literal

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from .contracts import StrictModel
from . import store

Amount = Annotated[float, Field(ge=0, le=10**13, allow_inf_nan=False, strict=True)]
Account = Literal['일반 계좌', 'ISA', '연금저축', 'IRP']


class Asset(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    instrument_id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=250, pattern=r'\S')
    account: Account
    category: Literal['주식', '채권', '금·원자재', '기타', '현금'] = '기타'
    currency: Literal['KRW', 'USD'] = 'KRW'
    quantity: Amount | None = None
    quantity_revision: str | None = Field(default=None, max_length=64)
    average_revision: str | None = Field(default=None, max_length=64)
    quantity_as_of: date | None = None
    average_as_of: date | None = None
    average: Amount | None = None
    price: Amount | None = None
    cost_fx: Annotated[float, Field(gt=0, le=100000, allow_inf_nan=False, strict=True)] | None = None
    amount: Amount | None = None
    weight: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)] | None = None
    target: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)] | None = None
    fee: Amount = 0
    price_date: date | None = None


class Dividend(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    date: date
    status: Literal['실수령', '예상']
    amount: Amount


class Month(StrictModel):
    month: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    value: Amount
    deposit: Amount = 0
    withdrawal: Amount = 0
    return_pct: Annotated[float, Field(ge=-100, le=10000, allow_inf_nan=False, strict=True)] | None = None


class Workbook(StrictModel):
    title: str = Field(default='내 포트폴리오', min_length=1, max_length=80, pattern=r'\S')
    fx: float | None = Field(default=None, gt=0, le=100000, allow_inf_nan=False, strict=True)
    assets: list[Asset] = Field(default_factory=list, max_length=30)
    dividends: list[Dividend] = Field(default_factory=list, max_length=300)
    months: list[Month] = Field(default_factory=list, max_length=60)

    @model_validator(mode='after')
    def references(self):
        ids = {a.id for a in self.assets}
        if len(ids) != len(self.assets) or len({d.id for d in self.dividends}) != len(self.dividends):
            raise ValueError('Duplicate record')
        if any(d.asset_id not in ids for d in self.dividends):
            raise ValueError('Dividend holding missing')
        if len({m.month for m in self.months}) != len(self.months):
            raise ValueError('Duplicate month')
        return self


class Save(StrictModel):
    version: int = Field(ge=0, strict=True)
    workbook: Workbook


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_workbooks (
        owner text PRIMARY KEY, version bigint NOT NULL, payload jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now())''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_workbook_archives (
        owner text NOT NULL, version bigint NOT NULL, payload jsonb NOT NULL,
        archived_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner,version))''')
    from . import dashboard_work
    dashboard_work.initialize(db)


def read(owner):
    with store.connect() as db:
        row = db.execute('SELECT version,payload AS workbook FROM portfolio_workbooks WHERE owner=%s', (owner,)).fetchone()
    if row:
        from .dashboard_work import identify
        row['workbook']['assets'] = [identify(a, strict=False, fx=row['workbook'].get('fx')) for a in row['workbook']['assets']]
    return row or {'version': 0, 'workbook': None}


def save(owner, body):
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (owner,))
        row = db.execute('SELECT version,payload FROM portfolio_workbooks WHERE owner=%s', (owner,)).fetchone()
        if (row['version'] if row else 0) != body.version:
            raise ValueError('WORKBOOK_CONFLICT')
        version = body.version + 1
        payload = body.workbook.model_dump(mode='json')
        from .dashboard_work import identify
        existing = {a['id']: a for a in row['payload']['assets']} if row else {}
        payload['assets'] = [identify(a, strict=existing.get(a['id'], {}).get('instrument_id') != a.get('instrument_id'), fx=payload.get('fx')) for a in payload['assets']]
        for asset in payload['assets']:
            previous = existing.get(asset['id'], {})
            entered = str(datetime.now(ZoneInfo('America/New_York' if asset['currency'] == 'USD' else 'Asia/Seoul')).date())
            for field in ('quantity', 'average'):
                changed = previous.get(field) != asset.get(field) or previous.get('instrument_id') != asset.get('instrument_id')
                changed |= previous.get(field + '_revision') != asset.get(field + '_revision')
                asset[field + '_as_of'] = entered if changed and asset.get(field) is not None else previous.get(field + '_as_of')
        if row and any(item not in payload[field] for field in ('dividends', 'months') for item in row['payload'].get(field, [])):
            db.execute('INSERT INTO portfolio_workbook_archives(owner,version,payload) VALUES (%s,%s,%s)', (owner, row['version'], Jsonb(row['payload'])))
        db.execute('''INSERT INTO portfolio_workbooks(owner,version,payload) VALUES (%s,%s,%s)
            ON CONFLICT(owner) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_at=now()''',
                   (owner, version, Jsonb(payload)))
        db.execute('UPDATE portfolio_workbooks SET refresh_after=now() WHERE owner=%s', (owner,))
        return {'version': version, 'workbook': payload}


def from_analysis(row):
    payload = row['input']
    percent = payload.get('input_mode') == 'percent'
    assets = []
    for i, item in enumerate(payload.get('holdings', [])):
        value = item.get('amount_krw')
        assets.append(Asset(id=uuid4().hex, instrument_id=item.get('instrument_id'), name=item['name'], account=item['account'],
                            currency='USD' if str(item.get('instrument_id', '')).startswith('US:') else 'KRW',
                            category='현금' if item.get('kind') == 'cash' else '기타',
                            amount=float(value) if value is not None and not percent else None,
                            weight=float(value) if value is not None and percent else None))
    return Workbook(assets=assets).model_dump(mode='json')
