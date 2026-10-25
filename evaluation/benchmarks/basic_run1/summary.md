# Incident benchmark **SIMULATED BENCHMARK: demo world DEMO-1, scripted operator (reviews with simulator ground truth after 3.0s) and scripted responder (acknowledges after 2.0s). Measures system latencies and counts, not human performance or real-video detection.** - Duration: 60 s wall clock; scripted actions: {'confirmed': 1, 'rejected': 3, 'approved': 1, 'acknowledged': 1}
- Candidates: {'total': 4, 'confirmed': 1, 'rejected': 3, 'pending': 0}
- Observations with a verified position: 4 of 4
- Detection to review: {'n': 4, 'median_s': 3.182, 'max_s': 3.354} (includes the scripted 3 s delay)
- Confirmation to alert: {'n': 1, 'median_s': 1.051, 'max_s': 1.051} (includes the next rules cycle and approval)
- Alert acknowledgment latency: {'n': 1, 'median_s': 2.06, 'max_s': 2.06} (scripted 2 s delay)
- Tracks: {'total': 4, 'loss_events': 1}
- Localization error: {'n': 3, 'median_m': 1.153, 'max_m': 1.903, 'source': 'simulator ground truth, matched by image box overlap (evaluation only)'}
- Metrics recomputed from the event log alone match the live values: True
