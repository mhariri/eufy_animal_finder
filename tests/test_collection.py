import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import homebase_files as files
from classify_homebase import apply_collection_policy,species_consensus


class ScreeningRules(unittest.TestCase):
    def test_person_vetoes_animal_in_a_different_view(self):
        report={'kind':'animal','topic':'cat','animalMax':.95,'frames':[
            {'detections':[{'category':'1','conf':.95}]},
            {'detections':[{'category':'2','conf':.11}]},
        ]}
        self.assertEqual(apply_collection_policy(report)['kind'],'person')

    def test_species_needs_temporal_agreement(self):
        selected=[(.9,None,n,0,[]) for n in [0,5,10,15]]
        cat=[('cat',.35),('dog',.25)]
        dog=[('dog',.35),('cat',.25)]
        self.assertTrue(species_consensus(selected,[cat]*4)['accepted'])
        self.assertFalse(species_consensus(selected,[cat,dog,cat,dog])['accepted'])
        self.assertFalse(species_consensus(selected[:1],[cat])['accepted'])


class CollectionPaths(unittest.TestCase):
    def test_publication_moves_birds_updates_metadata_and_keeps_names_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'state';videos=root/'videos'
            state.mkdir();recordings=state/'recordings';recordings.mkdir()
            bird=videos/'identified/birds'
            settings={'STATE':state,'INDEX':state/'video-filenames.json',
                      'EXCLUSIONS':state/'manual-exclusions.json','RECORDINGS':recordings,
                      'VIDEOS':videos,'IDENTIFIED_VIDEOS':videos/'identified','BIRD_VIDEOS':bird,
                      'PERSON_VIDEOS':state/'person-videos','PENDING_VIDEOS':state/'pending-videos'}
            with patch.multiple(files,**settings):
                names=[]
                for clip_id in ['0'*20,'1'*20]:
                    folder=recordings/clip_id;folder.mkdir()
                    (folder/'metadata.json').write_text('{}')
                    source=folder/'source.mp4';source.write_bytes(b'synthetic placeholder')
                    result=files.publish(clip_id,source,'2030-01-02T03:04:05','jay')
                    self.assertEqual(result['storage'],'birds')
                    self.assertTrue((bird/result['filename']).exists())
                    meta=json.loads((folder/'metadata.json').read_text())
                    self.assertEqual(files.video_path(meta),bird/result['filename'])
                    names.append(result['filename'])
                self.assertEqual(len(set(names)),2)
                result=files.publish('0'*20,None,'2030-01-02T03:04:05','person')
                self.assertTrue(Path(result['path']).is_file())
                self.assertFalse((bird/names[0]).exists())
                files.save_json(state/'manual-exclusions.json',{'1'*20:{'decision':'no-animal'}})
                with self.assertRaisesRegex(ValueError,'manually removed'):
                    files.publish('1'*20,None,'2030-01-02T03:04:05','cat')


if __name__=='__main__':unittest.main()
