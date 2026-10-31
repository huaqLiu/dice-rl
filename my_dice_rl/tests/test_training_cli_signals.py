"""Real CLI signal round trip with a tiny synthetic CPU model, never a GPU/robot."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import yaml

from my_dice_rl.script.process_gear_dataset import convert_dataset
from my_dice_rl.util.checkpoint import load_checkpoint
from my_dice_rl.util.config import load_train_config,PROJECT_ROOT


def test_usr1_finishes_update_and_writes_resumable_checkpoint(collected_episodes,tmp_path):
    dataset=tmp_path/'data.zarr'
    convert_dataset(collected_episodes,dataset,allow_demo=True)
    config=load_train_config(overrides=['task.allow_demo=true','training.device=cpu',
        'training.num_workers=0','training.epochs=1000','training.batch_size=2',
        'training.use_amp=false','training.checkpoint_every=1','training.max_val_steps=1',
        'model.image_size=32','model.down_dims=[16,32]','model.time_embed_dim=16',
        'model.train_diffusion_steps=4','model.inference_steps=2',
        'task.horizon_steps=8','task.act_steps=2'])
    config['task']['dataset_path']=str(dataset)
    config['training']['output_root']=str(tmp_path/'runs')
    cfg=tmp_path/'config.yaml';cfg.write_text(yaml.safe_dump(config))
    result=tmp_path/'result.json'
    env=dict(os.environ,CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
    with (tmp_path/'train.log').open('w') as log:
        child=subprocess.Popen([sys.executable,'-m','my_dice_rl.script.train_dp',
            '--config',str(cfg),'--result',str(result)],cwd=PROJECT_ROOT.parent,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+40.
            ready=False
            while time.monotonic()<deadline and child.poll() is None:
                if result.exists():
                    state=json.loads(result.read_text())
                    progress=Path(state.get('run_dir',tmp_path/'not_started'))/'progress.json'
                    if progress.exists() and json.loads(progress.read_text()).get('global_step',0)>=2:
                        ready=True
                        break
                time.sleep(.02)
            assert ready,(tmp_path/'train.log').read_text()[-3000:]
            child.send_signal(signal.SIGUSR1)
            assert child.wait(timeout=20.)==0,(tmp_path/'train.log').read_text()[-3000:]
        finally:
            if child.poll() is None:
                child.kill()  # Only this synthetic CPU test child.
                child.wait()
    final=json.loads(result.read_text())
    assert final['state']=='paused' and not final['training_complete']
    saved=load_checkpoint(final['last_checkpoint'])
    assert saved['global_step']>=2 and not saved['training_complete']
    assert saved['config']['task']['allow_demo'] and saved['config']['training']['device']=='cpu'
