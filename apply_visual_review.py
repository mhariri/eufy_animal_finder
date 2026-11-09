#!/usr/bin/env python3
"""Apply direct visual review decisions with stable clip IDs and preserved model evidence."""
import argparse
import json
import time
from collections import Counter
from homebase_files import STATE,RECORDINGS,publish,save_json,PROJECT,storage_root,INDEX
from classify_homebase import rebuild_indexes
import os


def main():
    parser=argparse.ArgumentParser();parser.add_argument('decisions');args=parser.parse_args()
    decisions=json.load(open(args.decisions))
    record_path=STATE/'manual-visual-decisions.json'
    saved=json.loads(record_path.read_text()) if record_path.exists() else {}
    backup=STATE/'before-visual-review';backup.mkdir(exist_ok=True,mode=0o700)
    counts=Counter()
    for clip_id,decision in decisions.items():
        rp=STATE/'analyses'/f'{clip_id}.json';mp=RECORDINGS/clip_id/'metadata.json'
        report=json.loads(rp.read_text());meta=json.loads(mp.read_text())
        if not (backup/f'{clip_id}.json').exists():save_json(backup/f'{clip_id}.json',report)
        topic=decision['topic']
        kind='person' if topic=='person' else 'manual-no-animal' if topic=='no-animal' else 'uncertain' if topic=='uncertain' else 'animal'
        named=publish(clip_id,None,meta['record']['start_time'],topic)
        decision.update(reviewedAt=time.time(),reviewer='assistant-direct-visual-review')
        report.update(kind=kind,topic=topic,videoFile=named['filename'],videoStorage=named['storage'],
                      automatic=False,manualVisualReview=decision)
        if kind=='animal':report.update(species=topic,speciesSource='direct-visual-frame-review')
        else:report.pop('species',None)
        save_json(rp,report);saved[clip_id]=decision;counts[topic]+=1
    save_json(record_path,saved)
    index=json.loads(INDEX.read_text());targets={v['filename']:storage_root(v.get('storage'))/v['filename'] for v in index.values()}
    # Existing collection symlinks retain their clip identity even after a rename.
    for link in (PROJECT/'wildlife_results/collection').rglob('*'):
        if link.is_symlink():
            clip_id=link.stem
            if clip_id in index:
                item=index[clip_id];target=storage_root(item.get('storage'))/item['filename']
                if target.exists():link.unlink();link.symlink_to(os.path.relpath(target,link.parent))
    rebuild_indexes()
    print(json.dumps({'reviewed':len(decisions),'topics':dict(counts)},indent=2))


if __name__=='__main__':main()
