#!/usr/bin/env python3
"""Build the pinned SDK fork needed for authenticated HomeBase 3 archive chunks."""
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SDK=ROOT/'vendor/eufy-sdk'
REVISION='d81bb0f1422d439d142e9269626997332f70ae24'


def run(*args,cwd=ROOT):
    subprocess.run(args,cwd=cwd,check=True)


if __name__=='__main__':
    SDK.parent.mkdir(exist_ok=True)
    if not SDK.exists():run('git','clone','https://github.com/mhariri/eufy-sdk.git',str(SDK))
    run('git','checkout','--detach',REVISION,cwd=SDK)
    run('npm','ci','--ignore-scripts',cwd=SDK)
    run('npm','run','build',cwd=SDK)
    run('npm','install','--ignore-scripts',cwd=ROOT/'eufy-downloader')
