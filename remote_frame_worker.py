#!/usr/bin/env python3
"""Mac worker: receives only JPEG samples and opaque job IDs, and returns classifications."""
import fcntl
import argparse
import json
import os
import re
import signal
import time
from pathlib import Path

os.environ.setdefault('EUFY_DETECTOR','md_v5a.0.1.pt')
os.environ.setdefault('EUFY_DETECTOR_SIZE','1280')
os.environ.setdefault('EUFY_USE_MPS','1')
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK','1')
os.environ.setdefault('HF_HUB_OFFLINE','1')
from classify_homebase import Reviewer,PRECISION_POLICY
from homebase_files import save_json

ROOT=Path(__file__).resolve().parent
STOP=False

def stop(*_):
    global STOP
    STOP=True


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker-id',type=int,default=0)
    worker_id=parser.parse_args().worker_id
    lock=open(ROOT/f'worker-{worker_id}.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('A frame worker is already running')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    (ROOT/'results').mkdir(exist_ok=True)
    (ROOT/'claims').mkdir(exist_ok=True)
    heartbeat=ROOT/f'heartbeat-{worker_id}.json'
    reviewer=Reviewer()
    print('Worker ready; compute device:',reviewer.device,flush=True)
    completed=0
    def beat(job=None):
        save_json(heartbeat,{'pid':os.getpid(),'workerId':worker_id,'time':time.time(),
                            'device':reviewer.device,'activeJob':job,'completed':completed})
    while not STOP:
        ready=[p for p in (ROOT/'inbox').iterdir() if p.is_dir() and re.fullmatch('[a-f0-9]{20}',p.name) and (p/'READY').exists() and not (ROOT/'results'/f'{p.name}.json').exists()]
        for folder in sorted(ready):
            if STOP:break
            claim=open(ROOT/'claims'/f'{folder.name}.lock','a')
            try:fcntl.flock(claim,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                claim.close();continue
            if not folder.exists() or (ROOT/'results'/f'{folder.name}.json').exists():
                claim.close();continue
            beat(folder.name)
            try:
                job=json.loads((folder/'job.json').read_text())
                if job['id']!=folder.name:raise ValueError('Job ID mismatch')
                if any(Path(f).name!=f or not f.endswith('.jpg') for f in job['frames']):raise ValueError('Invalid sample filenames')
                names=job['frames']
                if job.get('policy','').startswith('two-fps-'):names=names[::2]
                precision=job.get('reviewStage')=='precision'
                report=reviewer.analyze([folder/f for f in names],folder.name,job.get('manual'),high_precision=precision)
                report['reviewStage']=job.get('reviewStage','initial')
                report['samplingFps']=5 if precision else 1
                if precision:report['precisionPolicy']=PRECISION_POLICY
                report['worker']='mac-'+reviewer.device
                report['workerId']=worker_id
                for f in report['frames']:f['file']=Path(f['file']).name
                save_json(ROOT/'results'/f'{folder.name}.json',report)
                print('Reviewed',folder.name,report['topic'],flush=True)
                completed+=1
            except Exception as error:
                save_json(ROOT/'results'/f'{folder.name}.json',{'id':folder.name,'complete':False,'error':str(error),'worker':'mac','createdAt':time.time()})
                print('Job failed:',str(error),flush=True)
            finally:claim.close()
        beat()
        if not ready:
            if (ROOT/'STOP').exists():break
            time.sleep(2)
    print('Worker stopped',flush=True)


if __name__=='__main__':main()
