#!/usr/bin/env python3
"""Create private local account and optional SSH worker configuration interactively."""
import getpass
import json
from pathlib import Path
from homebase_files import STATE,save_json


if __name__=='__main__':
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    credentials=STATE/'credentials.json'
    if not credentials.exists():
        email=input('Eufy email: ').strip()
        password=getpass.getpass('Eufy password: ')
        country=input('Country code [US]: ').strip().upper() or 'US'
        save_json(credentials,{'username':email,'password':password,'country':country})
    else:print('Existing credentials preserved.')
    host=input('Optional SSH review worker (user@host; blank for local review): ').strip()
    if host:
        remote=input('Absolute project path on worker: ').strip()
        if not Path(remote).is_absolute():raise SystemExit('Worker path must be absolute')
        save_json(STATE/'worker-config.json',{'host':host,'remoteRoot':remote})
        print('Configure key-based SSH and copy the verified host key into:',STATE/'mac-known-hosts')
    print('Private configuration saved under:',STATE)
