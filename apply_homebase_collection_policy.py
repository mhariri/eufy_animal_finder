#!/usr/bin/env python3
"""Apply the current collection rule to saved frame detections without downloading again."""
import json

from classify_homebase import ANALYSES,apply_collection_policy,rebuild_indexes
from homebase_files import RECORDINGS,publish,save_json,video_path


def main():
    changed=0
    for path in ANALYSES.glob('*.json'):
        report=json.loads(path.read_text())
        if not report.get('complete'):continue
        old_kind=report['kind']
        report=apply_collection_policy(report)
        if old_kind!=report['kind']:
            meta_path=RECORDINGS/path.stem/'metadata.json'
            meta=json.loads(meta_path.read_text())
            if video_path(meta).exists():
                result=publish(path.stem,None,meta['record']['start_time'],report['topic'])
                report.update(videoFile=result['filename'],videoStorage='person')
            changed+=1
        save_json(path,report)
    print(json.dumps({'newlyExcludedForPerson':changed,'progress':rebuild_indexes()},indent=2))


if __name__=='__main__':main()
