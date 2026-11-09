#!/usr/bin/env python3
"""Resume HomeBase retrieval and private frame categorization on the authorized Mac."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

os.environ.setdefault('EUFY_DETECTOR','md_v5a.0.1.pt')
os.environ.setdefault('EUFY_DETECTOR_SIZE','1280')
from classify_homebase import POLICY,PREVIOUS_DENSE_POLICY,PRECISION_POLICY,samples,rebuild_indexes,apply_collection_policy
from homebase_files import PROJECT,STATE,RECORDINGS,VIDEOS,publish,save_json

STOP=False

def stop(*_):
    global STOP
    STOP=True


def load(path,default=None):
    try:return json.loads(path.read_text())
    except (FileNotFoundError,json.JSONDecodeError):return default


class Mac:
    def __init__(self):
        config=load(STATE/'worker-config.json')
        self.host=config['host'];self.root=config['remoteRoot']
        self.options=['-o','BatchMode=yes','-o','ConnectTimeout=10','-o',f'UserKnownHostsFile={STATE}/mac-known-hosts','-o','ControlMaster=auto','-o','ControlPersist=600','-o',f'ControlPath={STATE}/mac-control']
        self.staging=STATE/'mac-jobs';self.staging.mkdir(exist_ok=True,mode=0o700)
        self.received=STATE/'mac-results';self.received.mkdir(exist_ok=True,mode=0o700)
        self.pending=load(STATE/'mac-pending.json',{})

    def command(self,command,timeout=40):
        result=subprocess.run(['ssh',*self.options,self.host,command],capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise RuntimeError('Mac SSH command failed: '+result.stderr[-300:])
        return result.stdout

    def ensure_worker(self):
        root=shlex.quote(self.root)
        command=f'cd {root} && .venv/bin/python remote_worker_pool.py --workers 3'
        self.command(command)

    def enqueue(self,paths,manual):
        folders=[];new={}
        for meta_path in paths:
            folder=meta_path.parent;meta=load(meta_path)
            previous=load(STATE/'analyses'/f'{folder.name}.json',{})
            precision=previous.get('complete') and previous.get('policy')==POLICY and previous.get('kind')=='animal'
            stage='precision' if precision else 'initial'
            images=samples(meta,folder,5 if precision else 1)
            dest=self.staging/folder.name;dest.mkdir(exist_ok=True,mode=0o700)
            names=[]
            for k,image in enumerate(images):
                name=f'sample-{k+1:06d}.jpg';target=dest/name
                if target.exists():target.unlink()
                try:os.link(image,target)
                except OSError:shutil.copy2(image,target)
                names.append(name)
            save_json(dest/'job.json',{'id':folder.name,'frames':names,'manual':manual.get(folder.name,{}),'policy':POLICY,'reviewStage':stage})
            folders.append(dest);new[folder.name]={'queuedAt':time.time(),'reviewStage':stage}
        if not folders:return
        result=subprocess.run(['scp','-q','-r',*self.options,*map(str,folders),f'{self.host}:{self.root}/inbox/'],capture_output=True,text=True,timeout=90)
        if result.returncode:raise RuntimeError('Frame upload failed: '+result.stderr[-300:])
        self.command(' && '.join('touch '+shlex.quote(f'{self.root}/inbox/{f.name}/READY') for f in folders))
        self.pending.update(new);save_json(STATE/'mac-pending.json',self.pending)
        print('Queued sampled frames:',len(folders),'clips',flush=True)

    def collect(self):
        listing=self.command('ls -1 '+shlex.quote(self.root+'/results'))
        ids=[s[:-5] for s in listing.splitlines() if re.fullmatch(r'[a-f0-9]{20}\.json',s)]
        if not ids:return 0
        result=subprocess.run(['scp','-q',*self.options,f'{self.host}:{self.root}/results/*.json',str(self.received)+'/'],capture_output=True,text=True,timeout=90)
        if result.returncode:raise RuntimeError('Result download failed: '+result.stderr[-300:])
        applied=[]
        for clip_id in ids:
            result_path=self.received/f'{clip_id}.json';report=load(result_path)
            if not report or report.get('id')!=clip_id:continue
            folder=RECORDINGS/clip_id;meta=load(folder/'metadata.json')
            if not meta:continue
            previous=load(STATE/'analyses'/f'{clip_id}.json',{})
            precision=self.pending.get(clip_id,{}).get('reviewStage')=='precision'
            if report.get('complete') and report.get('policy')==PREVIOUS_DENSE_POLICY:
                report['previousScreeningPolicy']=report['policy'];report['policy']=POLICY
            if report.get('complete') and report.get('policy')==POLICY:
                if precision and report.get('precisionPolicy')!=PRECISION_POLICY:raise RuntimeError('Worker returned incompatible precision results')
                if precision:
                    backups=STATE/'initial-analyses';backups.mkdir(exist_ok=True,mode=0o700)
                    if not (backups/f'{clip_id}.json').exists():save_json(backups/f'{clip_id}.json',previous)
                report=apply_collection_policy(report)
                named=publish(clip_id,None,meta['record']['start_time'],report['topic'])
                for f in report['frames']:f['file']=str(folder/Path(f['file']).name)
                report['videoFile']=named['filename'];report['videoStorage']=named['storage']
                save_json(STATE/'analyses'/f'{clip_id}.json',report)
                print('Categorized:',named['filename'],'('+report['kind']+')',flush=True)
            else:
                if precision:
                    previous.update(precisionAttempts=previous.get('precisionAttempts',0)+1,precisionError=report.get('error','incompatible policy'))
                    report=previous
                else:report.update(complete=False,attempts=previous.get('attempts',0)+1)
                save_json(STATE/'analyses'/f'{clip_id}.json',report)
                print('Mac review deferred:',report.get('error','incompatible policy'),flush=True)
            applied.append(clip_id);self.pending.pop(clip_id,None)
            if self.staging.joinpath(clip_id).exists():shutil.rmtree(self.staging/clip_id)
        if applied:
            commands=[]
            for clip_id in applied:
                commands.append('rm -rf '+shlex.quote(f'{self.root}/inbox/{clip_id}')+' '+shlex.quote(f'{self.root}/results/{clip_id}.json'))
            self.command(' && '.join(commands))
            save_json(STATE/'mac-pending.json',self.pending)
        return len(applied)


def manifest():
    entries={}
    for file in sorted((STATE/'archive-days').glob('*.json'),reverse=True):
        for row in load(file)['records']:
            if not row['storage_path']:
                if int(row.get('frame_num',0)) or int(row.get('folder_size',0)):raise RuntimeError('Recording metadata has video bytes but no storage path')
                continue
            clip_id=hashlib.sha256(row['storage_path'].encode()).hexdigest()[:20]
            if clip_id in entries and entries[clip_id]['storage_path']!=row['storage_path']:raise RuntimeError('Local clip ID collision')
            entries[clip_id]=row
    return entries


def notify(title,body):
    try:subprocess.run(['notify-send','-a','Eufy archive review',title,body],timeout=10,capture_output=True)
    except (OSError,subprocess.TimeoutExpired):pass


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--no-download',action='store_true');ap.add_argument('--limit',type=int);ap.add_argument('--batch-size',type=int,default=40);args=ap.parse_args()
    lock=open(STATE/'pipeline.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('The HomeBase pipeline is already running')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    (STATE/'analyses').mkdir(exist_ok=True,mode=0o700)
    source=manifest();mac=Mac();mac.ensure_worker()
    node=None;last_index=0.;last_status=0.;mac_errors=0;zero_progress=0;download_before=0;completed_before=None
    next_download_at=0.;download_outages=0
    marker=STATE/'download-finished.json'
    if marker.exists():marker.unlink()
    joblog=open(STATE/'download-worker.log','a',buffering=1)
    status={'state':'running','pid':os.getpid(),'startedAt':time.time(),'totalRecordings':len(source),'policy':POLICY}
    save_json(STATE/'pipeline-status.json',status)
    try:
        while not STOP and not (STATE/'pipeline-stop').exists():
            metadata={p.parent.name:p for p in RECORDINGS.glob('*/metadata.json')}
            reports={p.stem:load(p,{}) for p in (STATE/'analyses').glob('*.json')}
            done={k for k,v in reports.items() if v.get('complete') and v.get('policy')==POLICY}
            if completed_before is None:completed_before=len(done)
            if args.limit and len(done)-completed_before>=args.limit:break
            missing=set(source)-set(metadata)
            eligible=[k for k in missing if load(RECORDINGS/k/'error.json',{}).get('attempts',0)<3]
            if node and node.poll() is not None:
                gained=len(metadata)-download_before
                zero_progress=zero_progress+1 if gained==0 else 0
                print('Download batch ended; verified new clips:',gained,'exit:',node.returncode,flush=True)
                node=None
                if gained:
                    download_outages=0;status.update(downloadState='active',nextDownloadRetryAt=None)
                elif zero_progress>=8:
                    download_outages+=1
                    delay=min(1800,600*download_outages)
                    next_download_at=time.time()+delay;zero_progress=0
                    status.update(downloadState='waiting-to-retry',nextDownloadRetryAt=next_download_at)
                    print(f'Downloads made no progress; backing off for {delay} seconds while frame review continues.',flush=True)
                elif gained==0:next_download_at=time.time()+min(60,5*zero_progress)
            if not node and eligible and not args.no_download and time.time()>=next_download_at:
                if shutil.disk_usage(PROJECT).free<30*1024**3:raise RuntimeError('Less than 30 GiB free; stopping before filling the disk')
                download_before=len(metadata)
                env=os.environ.copy();env['EUFY_PRIORITY_ANIMALS']='1';env['EUFY_ATTEMPT_LIMIT']='3'
                node=subprocess.Popen(['node','homebase_archive.mjs','download-batch',str(args.batch_size)],cwd=PROJECT/'eufy-downloader',env=env,stdout=joblog,stderr=subprocess.STDOUT)
                status.update(downloadState='attempting',nextDownloadRetryAt=None)
                print('Started checkpointed download batch',flush=True)
            if not node and (not eligible or args.no_download):save_json(marker,{'finishedAt':time.time(),'missing':len(missing),'mode':'samples-only' if args.no_download else 'full'})
            waiting=[p for clip_id,p in metadata.items() if clip_id not in done and clip_id not in mac.pending and reports.get(clip_id,{}).get('attempts',0)<3]
            refinement=[metadata[k] for k,r in reports.items() if os.environ.get('EUFY_PRECISION_PASS')=='1' and k in metadata and k not in mac.pending and r.get('complete') and r.get('policy')==POLICY and r.get('kind')=='animal' and r.get('precisionPolicy')!=PRECISION_POLICY and r.get('precisionAttempts',0)<3]
            refinement.sort(key=lambda p:reports[p.parent.name].get('topic')=='unidentified-animal')
            waiting=refinement+waiting
            if args.limit:waiting=waiting[:max(0,args.limit-(len(done)-completed_before)-len(mac.pending))]
            manual=load(PROJECT/'wildlife_results/homebase_review/decisions.json',{})
            try:
                mac.collect()
                capacity=max(0,25-len(mac.pending))
                if waiting and capacity:mac.enqueue(waiting[:min(12,capacity)],manual)
                mac_errors=0
            except (RuntimeError,subprocess.TimeoutExpired,OSError) as error:
                mac_errors+=1;print('Mac connection retry:',str(error),flush=True)
                if mac_errors>=8:raise RuntimeError('Mac worker is unreachable after repeated retries')
                time.sleep(min(30,mac_errors*3))
            now=time.time()
            if now-last_index>=30:
                current=rebuild_indexes();last_index=now
                status.update(downloaded=len(metadata),reviewed=len(done),missingDownloads=len(missing),exhaustedDownloadRetries=len(missing)-len(eligible),macQueued=len(mac.pending),updatedAt=now)
                status.update(precisionReviewed=sum(r.get('precisionPolicy')==PRECISION_POLICY for r in reports.values()),precisionWaiting=len(refinement))
                save_json(STATE/'pipeline-status.json',status)
                (VIDEOS/'STATUS.md').write_text('# HomeBase review status\n\n'+f'Downloaded: {len(metadata):,} / {len(source):,} videos.\n\nCategorized: {len(done):,}. Mac jobs queued: {len(mac.pending):,}.\n\n'+'[Animal collection](INDEX.md) · [Rain-only candidates](RAIN_ONLY_CANDIDATES.md) · [Uncertain clips](UNCERTAIN.md)\n')
            if now-last_status>=60:
                mac.ensure_worker()
                print(f'Progress: {len(metadata)}/{len(source)} downloaded; {len(done)} categorized; {len(mac.pending)} Mac jobs queued.',flush=True);last_status=now
            if not node and marker.exists() and not waiting and not mac.pending:
                latest={p.stem:load(p,{}) for p in (STATE/'analyses').glob('*.json')}
                retryable=[k for k in metadata if not (latest.get(k,{}).get('complete') and latest.get(k,{}).get('policy')==POLICY) and latest.get(k,{}).get('attempts',0)<3]
                precision_remaining=os.environ.get('EUFY_PRECISION_PASS')=='1' and any(r.get('kind')=='animal' and r.get('precisionPolicy')!=PRECISION_POLICY and r.get('precisionAttempts',0)<3 for r in latest.values())
                if not retryable and not precision_remaining:break
            time.sleep(3)
        metadata={p.parent.name:p for p in RECORDINGS.glob('*/metadata.json')}
        reports={p.stem:load(p,{}) for p in (STATE/'analyses').glob('*.json')}
        done={k for k,v in reports.items() if v.get('complete') and v.get('policy')==POLICY}
        all_complete=not (set(source)-set(metadata)) and not (set(source)-done)
        state='complete' if all_complete else 'stopped' if STOP else 'sample-check-finished' if args.no_download or args.limit else 'needs-attention'
        current=rebuild_indexes(final=all_complete)
        status.update(state=state,downloaded=len(metadata),reviewed=len(done),finishedAt=time.time(),counts=current['counts'])
        save_json(STATE/'pipeline-status.json',status)
        (VIDEOS/'STATUS.md').write_text('# HomeBase review status\n\n'+json.dumps(status,indent=2)+'\n')
        if not args.no_download and not args.limit:
            body=f"{len(done):,}/{len(source):,} clips categorized. {current['counts'].get('animal',0):,} animal candidates; {current['counts'].get('rain-candidate',0):,} rain-only candidates. See clips/homebase/INDEX.md."
            notify('HomeBase review complete' if all_complete else 'HomeBase review needs attention',body)
        print('Pipeline status:',state,flush=True)
    except Exception as error:
        status.update(state='needs-attention',error=str(error),updatedAt=time.time());save_json(STATE/'pipeline-status.json',status)
        notify('HomeBase review needs attention',str(error))
        raise
    finally:
        if node and node.poll() is None:
            node.terminate()
            try:node.wait(timeout=15)
            except subprocess.TimeoutExpired:node.kill()
        try:mac.command('touch '+shlex.quote(mac.root+'/STOP'))
        except Exception:pass
        joblog.close()


if __name__=='__main__':main()
