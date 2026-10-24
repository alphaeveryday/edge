"""Large news corpora must cross a bounded transport without losing the tail."""
import pytest

from edge_analysis_v2.sources.news import rows


class BoundedTransport:
    def __init__(self, fail_after=None, require_complete_plan=False):
        self.offset = 0
        self.closed = False
        self.fail_after = fail_after
        self.require_complete_plan = require_complete_plan
        self.complete_plan = False
        self.timeout = '15s'
        self.data = [{'id': i} for i in range(2501)]

    def cursor(self, *, name=None):
        self.name = name
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def execute(self, statement, parameters=()):
        if statement == 'SHOW statement_timeout':
            return self
        if statement == "SET LOCAL statement_timeout = '30s'":
            self.timeout = '30s'
            return
        if statement == "SELECT set_config('statement_timeout', %s, true)":
            self.timeout = parameters[0]
            return
        if statement == 'SET LOCAL cursor_tuple_fraction = 1.0':
            self.complete_plan = True
            return
        if self.name is None:
            raise TimeoutError('Entire corpus cannot cross the transport in one statement')
        if self.require_complete_plan and not self.complete_plan:
            raise TimeoutError('Fast-start plan cannot finish the corpus within the statement budget')

    def fetchone(self):
        return {'statement_timeout': self.timeout}

    def fetchmany(self, size):
        assert self.timeout == '30s'  # Large news reads get headroom without relaxing subsequent source reads.
        assert size <= 1000  # The simulated transport can carry at most this many rows per FETCH.
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise TimeoutError('Interrupted transfer')
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result


@pytest.mark.parametrize('timeout', ['15s', '25s'])
def test_bounded_transfer_preserves_all_rows_beyond_first_and_second_batches(timeout):
    connection = BoundedTransport()
    connection.timeout = timeout
    assert rows(connection, 'SELECT source corpus', ()) == connection.data
    assert connection.closed
    assert connection.timeout == timeout


def test_interrupted_transfer_fails_instead_of_returning_a_partial_corpus():
    connection = BoundedTransport(fail_after=1000)
    with pytest.raises(TimeoutError, match='Interrupted transfer'):
        rows(connection, 'SELECT source corpus', ())
    assert connection.closed


def test_full_corpus_read_does_not_request_a_fast_start_partial_cursor_plan():
    connection = BoundedTransport(require_complete_plan=True)
    assert rows(connection, 'SELECT source corpus', ()) == connection.data
