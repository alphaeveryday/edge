from http.client import HTTPConnection
from threading import Thread
import json
import pytest
from investment_lab.dashboard import make_server


def test_local_dashboard_does_not_invent_performance_or_accept_cross_origin_writes(tmp_path):
    server=make_server(tmp_path,0)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    port=server.server_port
    try:
        connection=HTTPConnection('127.0.0.1',port)
        connection.request('GET','/api/state')
        response=connection.getresponse()
        state=json.loads(response.read())
        assert state['runs']==[]
        assert state['benchmark'] is None
        connection.request('POST','/api/drafts',body='{}',headers={'Origin':'https://evil.example','Content-Type':'application/json'})
        assert connection.getresponse().status==403
        assert not list(tmp_path.glob('drafts/*.json'))
    finally:
        server.shutdown()
        server.server_close()


def test_second_dashboard_cannot_reuse_the_same_port(tmp_path):
    server=make_server(tmp_path,0)
    try:
        with pytest.raises(OSError):
            make_server(tmp_path,server.server_port)
    finally:
        server.server_close()
