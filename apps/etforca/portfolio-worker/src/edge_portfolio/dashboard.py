"""One editable workbook per visitor, independent of analysis and email records."""
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from .contracts import StrictModel
from . import store

Amount = Annotated[float, Field(ge=0, le=10**13, allow_inf_nan=False, strict=True)]
Account = Literal['일반 계좌', 'ISA', '연금저축', 'IRP']


class Asset(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=250, pattern=r'\S')
    account: Account
    category: Literal['주식', '채권', '금·원자재', '기타', '현금'] = '기타'
    currency: Literal['KRW', 'USD'] = 'KRW'
    quantity: Amount | None = None
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


def read(owner):
    with store.connect() as db:
        row = db.execute('SELECT version,payload AS workbook FROM portfolio_workbooks WHERE owner=%s', (owner,)).fetchone()
    return row or {'version': 0, 'workbook': None}


def save(owner, body):
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (owner,))
        row = db.execute('SELECT version FROM portfolio_workbooks WHERE owner=%s', (owner,)).fetchone()
        if (row['version'] if row else 0) != body.version:
            raise ValueError('WORKBOOK_CONFLICT')
        version = body.version + 1
        payload = body.workbook.model_dump(mode='json')
        db.execute('''INSERT INTO portfolio_workbooks(owner,version,payload) VALUES (%s,%s,%s)
            ON CONFLICT(owner) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_at=now()''',
                   (owner, version, Jsonb(payload)))
        return {'version': version, 'workbook': payload}


def from_analysis(row):
    payload = row['input']
    percent = payload.get('input_mode') == 'percent'
    assets = []
    for i, item in enumerate(payload.get('holdings', [])):
        value = item.get('amount_krw')
        assets.append(Asset(id=str(i), name=item['name'], account=item['account'],
                            currency='USD' if str(item.get('instrument_id', '')).startswith('US:') else 'KRW',
                            category='현금' if item.get('kind') == 'cash' else '기타',
                            amount=float(value) if value is not None and not percent else None,
                            weight=float(value) if value is not None and percent else None))
    return Workbook(assets=assets).model_dump(mode='json')
