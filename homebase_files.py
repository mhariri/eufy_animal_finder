#!/usr/bin/env python3
"""Publish HomeBase videos in one dated, topic-named directory; keep provenance private."""
import argparse
import contextlib
import datetime
import fcntl
import json
import os
import re
from pathlib import Path

PROJECT=Path(__file__).resolve().parent
VIDEOS=PROJECT/'clips/homebase'
IDENTIFIED_VIDEOS=VIDEOS/'identified'
BIRD_VIDEOS=IDENTIFIED_VIDEOS/'birds'
BIRD_TOPICS={'crow','owl','small-bird','woodpecker','gull','pigeon','hawk','jay','stellers-jay',
             'chickadee','spotted-towhee','northern-flicker','sparrow'}
STATE=PROJECT/'eufy-downloader/.eufy-state'
RECORDINGS=STATE/'recordings'
INDEX=STATE/'video-filenames.json'
PERSON_VIDEOS=STATE/'person-videos'
PENDING_VIDEOS=STATE/'pending-videos'
EXCLUSIONS=STATE/'manual-exclusions.json'


def video_path(meta):
    return storage_root(meta.get('videoStorage'))/meta['videoFile']


def storage_root(storage):
    return {'person':PERSON_VIDEOS,'pending':PENDING_VIDEOS,'identified':IDENTIFIED_VIDEOS,'birds':BIRD_VIDEOS,'rejected':STATE/'rejected-videos'}.get(storage,VIDEOS)


def topic_storage(topic):
    if topic=='person':return 'person'
    if topic=='no-animal':return 'rejected'
    if topic in BIRD_TOPICS:return 'birds'
    if topic in {'bear','raccoon','skunk','squirrel','crow','owl','small-bird','woodpecker',
                 'gull','pigeon','hawk','rabbit','deer','coyote','fox','cat','dog','opossum',
                 'rat-or-mouse','otter-or-mink','bat','moth'}:return 'identified'
    return 'review'


def review_link(report):
    return {'identified':'identified/','birds':'identified/birds/'}.get(report.get('videoStorage'),'')+report['videoFile']


def save_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2)
    tmp.replace(path)


@contextlib.contextmanager
def locked():
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(STATE/'video-filenames.lock','a') as lock:
        os.chmod(lock.name,0o600)
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def publish(clip_id,source,start_time,topic):
    if not re.fullmatch('[a-f0-9]{20}',clip_id):raise ValueError('Invalid local clip ID')
    topic=re.sub('[^a-z0-9-]+','-',topic.lower()).strip('-') or 'uncertain'
    stamp=datetime.datetime.fromisoformat(start_time).strftime('%Y-%m-%d-%H-%M')
    VIDEOS.mkdir(parents=True,exist_ok=True,mode=0o700)
    with locked():
        exclusions=json.loads(EXCLUSIONS.read_text()) if EXCLUSIONS.exists() else {}
        if clip_id in exclusions:
            raise ValueError('Clip was manually removed as containing no animal; publication is blocked')
        index=json.loads(INDEX.read_text()) if INDEX.exists() else {}
        existing=index.get(clip_id)
        if existing and topic=='unreviewed' and existing['topic']!='unreviewed':topic=existing['topic']
        existing_root=storage_root(existing.get('storage') if existing else None)
        source=Path(source) if source else existing_root/existing['filename']
        destination_storage=topic_storage(topic)
        destination_root=storage_root(destination_storage)
        destination_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        used={v['filename'] for k,v in index.items() if k!=clip_id}
        number=1
        while True:
            suffix='' if number==1 else f'-{number:02d}'
            name=f'{stamp}_{topic}{suffix}.mp4'
            dest=destination_root/name
            own=bool(existing and existing['filename']==name and existing_root==destination_root)
            if name not in used and (not dest.exists() or own or source.resolve()==dest.resolve()):break
            number+=1
        if source.resolve()!=dest.resolve():os.replace(source,dest)
        if not dest.exists():raise FileNotFoundError('Published video is missing')
        index[clip_id]={'filename':name,'topic':topic,'start_time':start_time,'storage':destination_storage}
        save_json(INDEX,index)
        meta_path=RECORDINGS/clip_id/'metadata.json'
        if meta_path.exists():
            meta=json.loads(meta_path.read_text());meta['videoFile']=name;meta['videoStorage']=index[clip_id]['storage'];save_json(meta_path,meta)
        return {'filename':name,'path':str(dest),'topic':topic,'storage':destination_storage}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('clip_id');ap.add_argument('source');ap.add_argument('start_time');ap.add_argument('topic')
    args=ap.parse_args()
    print(json.dumps(publish(args.clip_id,args.source or None,args.start_time,args.topic)))


if __name__=='__main__':main()
