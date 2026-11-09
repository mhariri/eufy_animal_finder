#!/usr/bin/env python3
"""Ensure independent MPS workers, each with a singleton lock and shared job claims."""
import argparse
import fcntl
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=3)
    count=parser.parse_args().workers
    if not 1<=count<=8:raise SystemExit('Worker count must be between 1 and 8')
    root=Path(__file__).resolve().parent
    (root/'STOP').unlink(missing_ok=True)
    env=os.environ.copy()
    env.update(EUFY_USE_MPS='1',EUFY_DETECTOR='md_v5a.0.1.pt',EUFY_DETECTOR_SIZE='1280',
               HF_HUB_OFFLINE='1',PYTORCH_ENABLE_MPS_FALLBACK='1')
    for worker_id in range(1,count+1):
        with open(root/f'worker-{worker_id}.lock','a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                print('Running worker',worker_id);continue
        with open(root/f'worker-{worker_id}.log','a') as log:
            process=subprocess.Popen(['/usr/bin/caffeinate','-i',sys.executable,'-u',
                str(root/'remote_frame_worker.py'),'--worker-id',str(worker_id)],cwd=root,
                env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        (root/f'worker-{worker_id}.pid').write_text(str(process.pid))
        print('Started worker',worker_id)


if __name__=='__main__':main()
