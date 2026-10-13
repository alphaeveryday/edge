"""Local CLI. A dataset is imported by the trusted controller, never by the agent."""
import argparse
import json
from pathlib import Path
from .replay import create,resume,read_run,encode
from .benchmark import run_benchmark


def main():
    parser=argparse.ArgumentParser(prog='investment-lab')
    commands=parser.add_subparsers(dest='command',required=True)
    c=commands.add_parser('create')
    c.add_argument('--config',type=Path,required=True)
    c.add_argument('--dataset',type=Path,required=True)
    c.add_argument('--runs',type=Path,default=Path('runs'))
    r=commands.add_parser('resume')
    r.add_argument('run',type=Path)
    r.add_argument('--max-days',type=int)
    s=commands.add_parser('report')
    s.add_argument('run',type=Path)
    b=commands.add_parser('benchmark')
    b.add_argument('--out',type=Path,default=Path('runs/benchmark.json'))
    d=commands.add_parser('dashboard')
    d.add_argument('--runs',type=Path,default=Path('runs'))
    d.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if args.command=='create':
        print(create(args.runs,json.loads(args.config.read_text(encoding='utf-8')),json.loads(args.dataset.read_text(encoding='utf-8'))))
    elif args.command=='resume':
        from .agent.investment import model_decider
        config=json.loads((args.run/'config.json').read_text(encoding='utf-8'))
        resume(args.run,model_decider(args.run,config['model']),args.max_days)
        print(encode(read_run(args.run)['metrics']))
    elif args.command=='report':
        print(encode(read_run(args.run)))
    elif args.command=='benchmark':
        result=run_benchmark()
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(encode(result),encoding='utf-8')
        print(encode({k:v for k,v in result.items() if k not in ('cases','categories','bypass')}))
    else:
        from .dashboard import serve
        serve(args.runs,args.port)


if __name__=='__main__':
    main()
