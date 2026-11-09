#!/usr/bin/env python3
"""Record user-deleted review videos without inferring rain or deleting source recordings."""
import json
import time

from homebase_files import STATE, VIDEOS, INDEX, EXCLUSIONS, locked, save_json,storage_root


def record_missing():
    with locked():
        index=json.loads(INDEX.read_text())
        excluded=json.loads(EXCLUSIONS.read_text()) if EXCLUSIONS.exists() else {}
        added=[]
        for clip_id,item in index.items():
            if item.get('storage','review') not in ('review','identified','birds') or (storage_root(item.get('storage'))/item['filename']).exists():
                continue
            report_path=STATE/'analyses'/f'{clip_id}.json'
            if not report_path.exists():
                continue  # An unfinished classification is not a review decision.
            report=json.loads(report_path.read_text())
            if not report.get('complete'):
                continue
            if clip_id not in excluded:
                excluded[clip_id]={
                    'filename':item['filename'],'reviewedAt':time.time(),
                    'decision':'no-animal','source':'user-deleted-local-video',
                    'previousKind':report['kind'],'previousTopic':report['topic'],
                }
                added.append(clip_id)
            report.update(kind='manual-no-animal',automatic=False,
                          manualDecision=excluded[clip_id])
            save_json(report_path,report)
        save_json(EXCLUSIONS,excluded)
    return {'newManualExclusions':len(added),'totalManualExclusions':len(excluded)}


if __name__=='__main__':
    result=record_missing()
    from classify_homebase import rebuild_indexes
    result['progress']=rebuild_indexes()
    print(json.dumps(result,indent=2))
