#!/usr/bin/env python3
"""Local, resumable frame review of verified HomeBase downloads. Never deletes HomeBase footage."""
import argparse
import json
import math
import os
import signal
import subprocess
import time
from collections import Counter,defaultdict
from pathlib import Path

from homebase_files import PROJECT,STATE,RECORDINGS,VIDEOS,publish,save_json,video_path,review_link

os.environ.setdefault('MPLCONFIGDIR','/tmp/eufy-matplotlib')
os.environ.setdefault('HF_HOME',str(PROJECT/'.models/huggingface'))
ANALYSES=STATE/'analyses'
DETECTOR_FILE=os.environ.get('EUFY_DETECTOR','md_v5a.0.1.pt')
DETECTOR_SIZE=int(os.environ.get('EUFY_DETECTOR_SIZE','1280'))
POLICY=f'one-fps-three-views-{DETECTOR_FILE}-{DETECTOR_SIZE}-v4'
PREVIOUS_DENSE_POLICY=f'two-fps-three-views-{DETECTOR_FILE}-{DETECTOR_SIZE}-v3'
PERSON_EXCLUSION_THRESHOLD=.10
COLLECTION_POLICY='animal-without-person-v1'
PRECISION_POLICY='five-fps-species-consensus-v1'


def apply_collection_policy(report):
    """A person vetoes animal inclusion, including mixed person/animal clips."""
    report['collectionPolicy']=COLLECTION_POLICY
    if report.get('kind')=='manual-no-animal':return report
    person_max=max([float(report.get('personMax',0))]+[
        float(d['conf']) for frame in report.get('frames',[])
        for d in frame.get('detections',[]) if str(d['category'])=='2'
    ])
    if person_max>=PERSON_EXCLUSION_THRESHOLD:
        if report.get('kind')!='person':
            report.setdefault('beforePersonExclusion',{'kind':report.get('kind'),'topic':report.get('topic'),'species':report.get('species')})
        report.update(kind='person',topic='person',personMax=person_max,
                      exclusionReason='Person detected in at least one sampled view')
    return report


SPECIES={
 'bear':['a black bear in a wildlife camera photo','an infrared camera photo of a bear at night'],
 'raccoon':['a raccoon in a wildlife camera photo','an infrared photo of a raccoon with a striped tail'],
 'skunk':['a striped skunk in a wildlife camera photo','a black and white skunk photographed at night'],
 'squirrel':['a squirrel in a wildlife camera photo','a small squirrel with a bushy tail'],
 'crow':['a black crow or raven perched on a pipe','a wildlife camera photo of a crow or raven'],
 'owl':['an owl in a wildlife camera photo','a night camera photo of an owl'],
 'small-bird':['a small wild songbird perched outdoors','a sparrow or chickadee in a wildlife camera photo'],
 'woodpecker':['a woodpecker in a wildlife camera photo','a woodpecker perched outside a house'],
 'gull':['a seagull in a wildlife camera photo','a gull standing outside a house'],
 'pigeon':['a pigeon in a wildlife camera photo','a dove or pigeon perched on a house'],
 'hawk':['a hawk in a wildlife camera photo','a hawk or eagle perched outdoors'],
 'rabbit':['a rabbit in a wildlife camera photo','a wild rabbit with long ears'],
 'deer':['a deer in a wildlife camera photo','a deer standing in a residential garden'],
 'coyote':['a coyote in a wildlife camera photo','a night camera photo of a coyote'],
 'fox':['a fox in a wildlife camera photo','a fox with a bushy tail photographed at night'],
 'cat':['a domestic cat in a security camera photo','a cat walking outside a house at night'],
 'dog':['a domestic dog in a security camera photo','a dog walking outside a house'],
 'opossum':['an opossum in a wildlife camera photo','an opossum with a pale face photographed at night'],
 'rat-or-mouse':['a rat or mouse in a wildlife camera photo','a small rodent walking on the ground at night'],
 'otter-or-mink':['an otter or mink in a wildlife camera photo','a small dark mink with a long body'],
 'bat':['a bat in a wildlife camera photo','a bat flying outside a house at night'],
 'other-animal':['an unidentified wild animal in a camera trap photo','an animal outside a house'],
 'person-negative':['a person in a security camera photo','a person wearing an animal costume'],
 'empty-negative':['an empty porch with plants and no animals','an empty outdoor security camera view'],
 'artifact-negative':['an insect or spiderweb close to a security camera lens','a toy or statue shaped like an animal'],
}


def species_consensus(selected,ranked):
    """Require strong votes on distinct frames; CLIP similarities are not probabilities."""
    votes=defaultdict(list)
    for item,rank in zip(selected,ranked):
        label,score=rank[0];margin=score-rank[1][1]
        if item[0]>=.3 and label in SPECIES and not label.endswith('-negative') and label!='other-animal' and score>=.28 and margin>=.04:
            votes[label].append((item[2],score,margin))
    if not votes:return {'accepted':False,'reason':'No strong species votes','framesCompared':len(selected)}
    label,rows=max(votes.items(),key=lambda pair:(len(pair[1]),sum(r[1] for r in pair[1])))
    fraction=len(rows)/max(1,len(selected));span=(max(r[0] for r in rows)-min(r[0] for r in rows))/5
    accepted=len(rows)>=3 and fraction>=.65 and span>=.8
    return {'accepted':accepted,'species':label,'supportingFrames':len(rows),'framesCompared':len(selected),
            'agreement':fraction,'spanSeconds':span,'meanScore':sum(r[1] for r in rows)/len(rows)}
WEATHER={
 'rain':['rain falling in an empty outdoor security camera view','visible rain streaks in a night security camera image','heavy rain falling on an empty porch'],
 'dry':['an empty porch in dry weather','a clear outdoor security camera view with no rain','a dry empty walkway and plants'],
 'wind':['wind blowing leaves and branches in an empty security camera view','moving plants outside a house without rain'],
 'unclear':['a blurry dark security camera image','an obstructed security camera view with glare or insects'],
 'snow':['snow falling outside a house','snowflakes in an outdoor night security camera view'],
}
STOP=False

def stop(*_):
    global STOP
    STOP=True


def samples(meta,folder,fps=1):
    return dense_samples(meta,folder,fps)


def dense_samples(meta,folder,fps=1):
    if fps not in (1,5):raise ValueError('Unsupported sampling rate')
    cache=folder/f'dense-{fps}fps'
    marker=cache/'complete.json'
    if marker.exists():
        names=json.loads(marker.read_text())['frames']
        if names and all((cache/n).exists() for n in names):return [cache/n for n in names]
    cache.mkdir(exist_ok=True,mode=0o700)
    for p in cache.glob('frame-*.jpg'):p.unlink()
    command=['ffmpeg','-nostdin','-v','error','-threads','2','-i',str(video_path(meta)),
             '-vf',f"fps={fps},scale='min(1280,iw)':-2",'-q:v','3','-y',str(cache/'frame-%06d.jpg')]
    result=subprocess.run(command,capture_output=True,text=True,timeout=180)
    images=sorted(cache.glob('frame-*.jpg'))
    if result.returncode or result.stderr.strip() or not images:raise RuntimeError('Dense frame extraction failed: '+result.stderr[:200])
    save_json(marker,{'frames':[p.name for p in images]})
    return images


def legacy_samples(meta,folder):
    frames=meta.get('sampleFrames') or []
    if frames and all((folder/f['file']).exists() for f in frames):return [folder/f['file'] for f in frames]
    count=int(meta['videoFrames']);fps=float(meta['fps'])
    indices=sorted({min(count-1,math.floor(count*f)) for f in [.1,.5,.9]})
    old=[PROJECT/'wildlife_results/homebase_review'/f'{folder.name}-{k}.jpg' for k in range(3)]
    if len(indices)==3 and all(p.exists() for p in old):return old
    selection='+'.join(f'eq(n,{n})' for n in indices)
    command=['ffmpeg','-nostdin','-v','error','-threads','2','-i',str(video_path(meta)),'-vf',f"select='{selection}',scale='min(1280,iw)':-2",'-fps_mode','vfr','-q:v','2','-y',str(folder/'sample-%02d.jpg')]
    result=subprocess.run(command,capture_output=True,text=True,timeout=120)
    images=[folder/f'sample-{k+1:02d}.jpg' for k in range(len(indices))]
    if result.returncode or result.stderr.strip() or not all(p.exists() for p in images):raise RuntimeError('Frame extraction failed: '+result.stderr[:200])
    return images


class Reviewer:
    def __init__(self):
        import torch
        import open_clip
        from megadetector.detection.run_detector import load_detector
        self.torch=torch
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        use_mps=os.environ.get('EUFY_USE_MPS')=='1' and torch.backends.mps.is_available()
        self.detector=load_detector(str(PROJECT/'.models'/DETECTOR_FILE),force_cpu=not use_mps)
        device='mps' if use_mps else 'cpu'
        self.clip,_,self.preprocess=open_clip.create_model_and_transforms('ViT-B-32',pretrained='laion2b_s34b_b79k',device=device,cache_dir=str(PROJECT/'.models/clip'))
        self.clip.eval();self.device=device
        tokenizer=open_clip.get_tokenizer('ViT-B-32')
        self.features={}
        with torch.inference_mode():
            for name,groups in [('species',SPECIES),('weather',WEATHER)]:
                labels=[];vectors=[]
                for label,prompts in groups.items():
                    encoded=self.clip.encode_text(tokenizer(prompts).to(self.device))
                    encoded=encoded/encoded.norm(dim=-1,keepdim=True)
                    vector=encoded.mean(dim=0);vector=vector/vector.norm()
                    labels.append(label);vectors.append(vector)
                self.features[name]=(labels,torch.stack(vectors))

    def rank(self,images,group):
        if not images:return []
        torch=self.torch;labels,text=self.features[group]
        with torch.inference_mode():
            batch=torch.stack([self.preprocess(im) for im in images]).to(self.device)
            vectors=self.clip.encode_image(batch);vectors=vectors/vectors.norm(dim=-1,keepdim=True)
            scores=(vectors@text.T).cpu().tolist()
        return [sorted(zip(labels,row),key=lambda p:p[1],reverse=True) for row in scores]

    def review(self,meta,folder):
        paths=samples(meta,folder)
        manual_path=PROJECT/'wildlife_results/homebase_review/decisions.json'
        manual=json.loads(manual_path.read_text()).get(folder.name,{}) if manual_path.exists() else {}
        report=self.analyze(paths,folder.name,manual)
        result=publish(folder.name,None,meta['record']['start_time'],report['topic'])
        report['videoFile']=result['filename'];report['videoStorage']=result['storage']
        return report

    def analyze(self,paths,clip_id,manual=None,high_precision=False):
        from PIL import Image
        manual=manual or {}
        animal=[];max_person=0.;max_animal=0.;frame_results=[];weather_views=[]
        for frame_index,p in enumerate(paths):
            with Image.open(p) as loaded:whole=loaded.convert('RGB')
            for view in range(3):
                image=whole if view==2 else whole.crop((0,whole.height*view//2,whole.width,whole.height*(view+1)//2))
                result=self.detector.generate_detections_one_image(image,str(p),detection_threshold=.03,image_size=DETECTOR_SIZE)
                if result.get('failure'):raise RuntimeError('Object detector failed on a sampled frame')
                detections=result.get('detections',[])
                frame_results.append({'file':str(p),'sample':frame_index,'view':view,'detections':detections})
                if len(weather_views)<12:weather_views.append(image)
                for detection in detections:
                    category=str(detection['category']);confidence=float(detection['conf'])
                    if category=='2':
                        max_person=max(max_person,confidence)
                        if confidence>=PERSON_EXCLUSION_THRESHOLD:
                            return apply_collection_policy({'id':clip_id,'complete':True,'policy':POLICY,
                                'kind':'person','topic':'person','personMax':max_person,'animalMax':max_animal,
                                'frames':frame_results,'sampledFrames':frame_index+1,'sampledViews':len(frame_results),
                                'automatic':True,'createdAt':time.time(),'earlyPersonRejection':True})
                    if category!='1':continue
                    max_animal=max(max_animal,confidence)
                    if confidence<.05:continue
                    x,y,w,h=detection['bbox'];pad=.15
                    box=(max(0,int((x-w*pad)*image.width)),max(0,int((y-h*pad)*image.height)),min(image.width,int((x+w*(1+pad))*image.width)),min(image.height,int((y+h*(1+pad))*image.height)))
                    crop=image.crop(box)
                    if crop.width<2 or crop.height<2:continue
                    if not high_precision or frame_index%max(1,math.ceil(len(paths)/48))==0:
                        animal.append((confidence,crop,frame_index,view,detection['bbox']))
                        if high_precision and len(animal)>144:
                            animal=sorted(animal,key=lambda x:x[0],reverse=True)[:144]
        report={'id':clip_id,'complete':True,'policy':POLICY,'detector':f'{DETECTOR_FILE} / {DETECTOR_SIZE}','speciesModel':'CLIP ViT-B-32 laion2b_s34b_b79k','sampledFrames':len(paths),'sampledViews':len(frame_results),'animalMax':max_animal,'personMax':max_person,'frames':frame_results,'createdAt':time.time(),'automatic':True}
        if max_person>=PERSON_EXCLUSION_THRESHOLD:
            report.update(kind='person',topic='person')
        elif not high_precision and manual.get('decision')=='keep_animal' and 'Crow' in manual.get('reason',''):
            report.update(kind='animal',topic='crow',species='crow',speciesSource='manual-frame-review',automatic=False)
        elif manual.get('decision')=='keep' and 'Person' in manual.get('reason',''):
            report.update(kind='person',topic='person',automatic=False)
        elif animal:
            selected=sorted(animal,key=lambda x:x[0],reverse=True)[:4]
            if high_precision:
                by_frame={}
                for item in sorted(animal,key=lambda x:x[0],reverse=True):by_frame.setdefault(item[2],item)
                selected=sorted(by_frame.values(),key=lambda x:x[2])
            ranked=[]
            for offset in range(0,len(selected),8):ranked.extend(self.rank([x[1] for x in selected[offset:offset+8]],'species'))
            candidates=[]
            report['cropScores']=[{'animalDetection':item[0],'sample':item[2],'view':item[3],'bbox':item[4],'top':rank[:4]} for item,rank in zip(selected,ranked)]
            for rank in ranked:
                best,score=rank[0];margin=score-rank[1][1]
                if not best.endswith('-negative') and best!='other-animal' and max_animal>=.2 and score>=.25 and margin>=.025:candidates.append((score,best,margin))
            if high_precision:
                decision=species_consensus(selected,ranked)
                report['speciesEvidence']=decision
                if decision['accepted']:
                    label=decision['species']
                    report.update(kind='animal',topic=label,species=label,speciesSource='automatic-temporal-consensus',speciesScore=decision['meanScore'])
                else:report.update(kind='animal',topic='unidentified-animal',species='unidentified-animal',speciesSource='insufficient-temporal-agreement')
            elif candidates:
                score,label,margin=max(candidates)
                report.update(kind='animal',topic=label,species=label,speciesSource='automatic-tentative',speciesScore=score,speciesMargin=margin)
            else:report.update(kind='animal',topic='unidentified-animal',species='unidentified-animal',speciesSource='unresolved-candidate')
        elif max_person>=.25:
            report.update(kind='person',topic='person')
        else:
            ranked=self.rank(weather_views,'weather')
            averages={label:sum(dict(row)[label] for row in ranked)/len(ranked) for label in WEATHER}
            order=sorted(averages.items(),key=lambda x:x[1],reverse=True)
            report['weatherScores']=averages
            if order[0][0]=='rain' and order[0][1]-order[1][1]>=.015 and max_person<.15 and max_animal<.05:
                report.update(kind='rain-candidate',topic='rain-only',requiresReviewBeforeDeletion=True)
            else:report.update(kind='uncertain',topic='uncertain',reason='No person or animal found in samples; rain-only cause is not established')
        return apply_collection_policy(report)


def load_inventory_count():
    count_path=STATE/'video-inventory-count.json'
    if count_path.exists():return json.loads(count_path.read_text())['videos']
    rows=[r for p in (STATE/'archive-days').glob('*.json') for r in json.loads(p.read_text())['records']]
    videos=len({r['storage_path'] for r in rows if r['storage_path']})
    save_json(count_path,{'videos':videos,'metadataRows':len(rows),'nonVideoRows':sum(not r['storage_path'] for r in rows)})
    return videos


def rebuild_indexes(final=False):
    ANALYSES.mkdir(parents=True,exist_ok=True,mode=0o700)
    results=[]
    for p in ANALYSES.glob('*.json'):
        try:r=json.loads(p.read_text())
        except (OSError,json.JSONDecodeError):continue
        if r.get('complete') and r.get('policy')==POLICY:results.append(r)
    counts=Counter(r['kind'] for r in results)
    progress=json.loads((STATE/'archive-inventory-progress.json').read_text())
    inventory_manifest=load_inventory_count()
    total=inventory_manifest;downloaded=len(list(RECORDINGS.glob('*/metadata.json')))
    status={'inventory':total,'downloaded':downloaded,'reviewed':len(results),'counts':dict(counts),'final':final,'updatedAt':time.time()}
    save_json(STATE/'review-progress.json',status)
    animals=defaultdict(list)
    for r in results:
        if r['kind']=='animal':animals[r.get('species','unidentified-animal')].append(r)
    header=f'# HomeBase animal collection\n\n'+('Automated pass finished.' if final else 'In progress.')+f' Reviewed {len(results):,} of {total:,} inventoried recordings; downloaded {downloaded:,}.\n\nScreening uses one frame per second throughout each clip (earlier completed reviews used two), checking both camera halves and the full image. Only animal clips with no person detected in any sampled view qualify. Brief appearances may be missed. Automatic species labels are tentative; unidentified animals remain included for review.\n\n'
    lines=[header]
    for species,rows in sorted(animals.items()):
        lines.append(f'## {species.replace("-"," ").title()}\n\n')
        for r in sorted(rows,key=lambda r:r['videoFile']):
            label='confirmed by frame review' if not r.get('automatic',True) else 'automatic candidate'
            lines.append(f'- [{r["videoFile"]}]({review_link(r)}) — {label}.\n')
        lines.append('\n')
    if (VIDEOS/'identified/PHONE_EXPORT_ADDITIONS.md').exists():
        lines.append('[Additional reviewed animals from phone exports](identified/PHONE_EXPORT_ADDITIONS.md) — kept separately from HomeBase archive coverage.\n\n')
    lines.append('[Rain-only candidates](RAIN_ONLY_CANDIDATES.md) · [Uncertain clips](UNCERTAIN.md)\n')
    (VIDEOS/'INDEX.md').write_text(''.join(lines))
    rain=['# Rain-only candidates\n\nFrame-based candidates with no person or animal detected in the sampled views. Review before deleting from HomeBase. No HomeBase deletion is performed by this pipeline.\n\n']
    uncertain=['# Uncertain clips\n\nPreserved for review. These have not been established to be rain-only.\n\n']
    for r in sorted(results,key=lambda r:r['videoFile']):
        if r['kind']=='rain-candidate':rain.append(f'- [{r["videoFile"]}]({r["videoFile"]})\n')
        if r['kind']=='uncertain':uncertain.append(f'- [{r["videoFile"]}]({r["videoFile"]})\n')
    (VIDEOS/'RAIN_ONLY_CANDIDATES.md').write_text(''.join(rain));(VIDEOS/'UNCERTAIN.md').write_text(''.join(uncertain))
    collection=PROJECT/'wildlife_results/collection';collection.mkdir(parents=True,exist_ok=True)
    relative=os.path.relpath(VIDEOS,collection)
    animal_index=''.join(lines)
    for r in results:animal_index=animal_index.replace(f']({review_link(r)})',f']({relative}/{review_link(r)})')
    animal_index=animal_index.replace('](identified/PHONE_EXPORT_ADDITIONS.md)',f']({relative}/identified/PHONE_EXPORT_ADDITIONS.md)')
    animal_index=animal_index.replace('](RAIN_ONLY_CANDIDATES.md)',f']({relative}/RAIN_ONLY_CANDIDATES.md)').replace('](UNCERTAIN.md)',f']({relative}/UNCERTAIN.md)')
    (collection/'INDEX.md').write_text(animal_index)
    return status


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--watch',action='store_true');ap.add_argument('--limit',type=int);ap.add_argument('--indexes-only',action='store_true');args=ap.parse_args()
    ANALYSES.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.indexes_only:print(json.dumps(rebuild_indexes()));return
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    reviewer=Reviewer();processed=0
    while not STOP:
        work=0
        for metadata_path in sorted(RECORDINGS.glob('*/metadata.json')):
            if STOP or (args.limit and processed>=args.limit):break
            folder=metadata_path.parent;out=ANALYSES/f'{folder.name}.json'
            old=json.loads(out.read_text()) if out.exists() else {}
            if old.get('complete') and old.get('policy')==POLICY:continue
            if old.get('attempts',0)>=3:continue
            try:
                meta=json.loads(metadata_path.read_text());report=reviewer.review(meta,folder)
                save_json(out,report);processed+=1;work+=1
                print(f"Reviewed {folder.name}: {report['topic']}",flush=True)
            except Exception as error:
                save_json(out,{'id':folder.name,'complete':False,'attempts':old.get('attempts',0)+1,'error':str(error),'updatedAt':time.time()})
                print(f'Review deferred: {error}',flush=True)
            if processed%20==0:rebuild_indexes()
        rebuild_indexes()
        if args.limit and processed>=args.limit:break
        if not args.watch:break
        if (STATE/'download-finished.json').exists() and work==0:break
        if work==0:time.sleep(5)
    print('Frame review worker stopped.',flush=True)


if __name__=='__main__':main()
