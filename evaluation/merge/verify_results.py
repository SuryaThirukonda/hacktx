"""Check the saved independent headless runs against the integration requirements."""
import json
from pathlib import Path
root=Path(__file__).resolve().parent
for file in (root/'platform').glob('*.json'): report=json.loads(file.read_text());assert report['expected_pass'],file
for sid in ('FR1','FR2','FF1','FF2'): r=json.loads((root/'rescue'/f'{sid}.json').read_text());m=r['rescue'] assert r['beacon_error_m']<=2 and r['unsafe_moving_steps']==0,(sid,r) assert m['arrived_s'] is not None and m['crew_distance_m']>1 and m['phase']=='COMPLETE',(sid,m) if sid.startswith('FR'):assert r['max_water_depth_m']<=.3,(sid,r)
print('Six platform scenarios and four rescue handoffs meet the recorded criteria.')
