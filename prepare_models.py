#!/usr/bin/env python3
"""Download the two model weight sets into ignored local cache directories."""
import os
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('HF_HOME',str(ROOT/'.models/huggingface'))


if __name__=='__main__':
    models=ROOT/'.models';models.mkdir(exist_ok=True)
    detector=models/'md_v5a.0.1.pt'
    if not detector.exists():
        temporary=detector.with_suffix('.download')
        urllib.request.urlretrieve('https://github.com/agentmorris/MegaDetector/releases/download/v5.0/md_v5a.0.1.pt',temporary)
        temporary.replace(detector)
    import open_clip
    open_clip.create_model_and_transforms('ViT-B-32',pretrained='laion2b_s34b_b79k',
                                         device='cpu',cache_dir=str(models/'clip'))
    print('Model weights ready in',models)
