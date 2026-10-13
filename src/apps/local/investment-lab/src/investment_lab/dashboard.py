"""Loopback-only human audit UI, isolated from the research model tool surface."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
from threading import Thread, Lock
import socket
from uuid import uuid4
from .replay import create, read_run, resume, encode


def make_server(root,port):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    assets=Path(__file__).parent/'static'
    token=secrets.token_urlsafe(32)
    active=set()
    lock=Lock()

    def load(path):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def reply(self,status,value,content_type='application/json'):
            body=(encode(value) if content_type=='application/json' else value).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def trusted(self,write=False):
            host=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin') not in (None,'http://'+host):
                self.reply(403,{'error':'Local origin required'})
                return False
            if write and not secrets.compare_digest(self.headers.get('X-Lab-Token',''),token):
                self.reply(403,{'error':'Local form token required'})
                return False
            return True

        def do_GET(self):
            if not self.trusted():
                return
            try:
                if self.path in ('/','/app.js','/style.css'):
                    name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[self.path]
                    content=(assets/name).read_text(encoding='utf-8').replace('__TOKEN__',token)
                    self.reply(200,content,{'html':'text/html','js':'text/javascript','css':'text/css'}[name.split('.')[-1]])
                elif self.path=='/api/state':
                    runs=[]
                    for path in sorted(root.glob('*/ledger.sqlite')):
                        if re.fullmatch('[0-9a-f]{32}',path.parent.name):
                            run=read_run(path.parent)
                            runs.append({k:v for k,v in run.items() if k!='days'} | dict(progress=len(run['days'])))
                    self.reply(200,dict(runs=runs,benchmark=load(root/'benchmark.json'),access=load(root/'access'/'db-probe.json'),
                        readiness=load(root/'access'/'readiness.json'),
                        datasets=[p.stem for p in sorted((root/'datasets').glob('*.json'))],
                        drafts=[load(p) for p in sorted((root/'drafts').glob('*.json'))],active=sorted(active)))
                elif re.fullmatch('/api/runs/[0-9a-f]{32}',self.path):
                    self.reply(200,read_run(root/self.path.rsplit('/',1)[-1]))
                else:
                    self.reply(404,{'error':'Not found'})
            except (OSError,ValueError):
                self.reply(422,{'error':'Local artifact could not be read'})

        def do_POST(self):
            if not self.trusted(write=True):
                return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100_000 or self.headers.get('Content-Type')!='application/json':
                    raise ValueError('Bounded JSON body required')
                body=json.loads(self.rfile.read(size))
                if self.path=='/api/drafts':
                    instructions=body.get('instructions')
                    if not isinstance(instructions,str) or not 1<=len(instructions.strip())<=20000:
                        raise ValueError('Instructions must contain 1..20000 characters')
                    draft=dict(id=uuid4().hex,instructions=instructions,status='draft')
                    (root/'drafts').mkdir(exist_ok=True)
                    (root/'drafts'/f'{draft["id"]}.json').write_text(encode(draft),encoding='utf-8')
                    self.reply(201,draft)
                elif self.path=='/api/runs':
                    dataset=body['dataset']
                    if not isinstance(dataset,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',dataset):
                        raise ValueError('Registered dataset name required')
                    folder=create(root,body['config'],load(root/'datasets'/f'{dataset}.json'))
                    self.reply(201,dict(id=folder.name))
                elif re.fullmatch('/api/runs/[0-9a-f]{32}/resume',self.path):
                    identity=self.path.split('/')[3]
                    max_days=body.get('max_days',3)
                    if type(max_days) is not int or not 1<=max_days<=520:
                        raise ValueError('Daily run budget must be 1..520')
                    folder=root/identity
                    run=read_run(folder)
                    from .agent.investment import model_decider
                    decider=model_decider(folder,run['config']['model'])
                    with lock:
                        if identity in active:
                            self.reply(409,{'error':'Already executing'})
                            return
                        active.add(identity)
                    def work():
                        try:
                            resume(folder,decider,max_days)
                        finally:
                            with lock:
                                active.discard(identity)
                    Thread(target=work,daemon=True).start()
                    self.reply(202,dict(id=identity))
                else:
                    self.reply(404,{'error':'Not found'})
            except (ValueError,KeyError,TypeError,OSError):
                self.reply(422,{'error':'Check input, dataset readiness and model credentials. Existing runs were not modified.'})

    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address=False

        def server_bind(self):
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()

    return LocalServer(('127.0.0.1',port),Handler)


def serve(root,port):
    server=make_server(root,port)
    print(f'Investment Lab: http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
