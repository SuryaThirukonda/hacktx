"""Camera coverage must retain visible history and exclude a roof-hidden ground cell."""
import sys,json
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'backend'))
from overwatch.sim.world import World,BoxObj
from overwatch.sim.survey import CameraSurvey
from overwatch.geometry.camera import Intrinsics,CameraPose,PinholeCamera
w=World(bounds=(-20,20,-20,20));w.boxes=[BoxObj('roof','building',(0,0),(8,8,5))]
s=CameraSurvey(w);camera=PinholeCamera(Intrinsics.from_hfov(640,480,75),CameraPose(np.array([0.,0.,20.]),0,-90))
s.update(1,camera);covered=s.last>-1e8
assert covered.any()
inside_roof=(np.abs(s.pts[:,0])<3)&(np.abs(s.pts[:,1])<3)
assert not covered[inside_roof].any()
camera=PinholeCamera(camera.intr,CameraPose(np.array([0.,0.,20.]),0,90))
s.update(5,camera);assert np.array_equal(covered,s.last>-1e8)
assert s.encode(5)['seen_m2']>0
print(json.dumps({'roof_ground_excluded':True,'visible_ground_retained':True,'seen_m2':s.encode(5)['seen_m2']}))
