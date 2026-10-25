# test12: police drone dispatch (simulated)

A self-contained scenario on its own page, `http://localhost:8000/test12` (start the backend with `make backend`).
Click the map to choose where the suspect starts (or leave it random), then press **Start simulation**. Every run is
different (a random seed, shown on the page; type one in to replay it). The run goes on until **Stop** (or half an
hour).

1. **911 call.** A robbery: the suspect wears a red shirt and was last seen running one way down a street, a few
   seconds ago, at the point picked on the map or somewhere 90 to 250 m from the police station.
2. **Dispatch.** The drone (a DJI Mini 2 stand-in) climbs straight up from the pad beside the station to 30 m.
3. **Search.** Where could they be by now? 1,500 guesses spread out along the sidewalks from the reported spot at
   running speed, a few ducking into cover. The drone flies to where its camera would cover the most of them by the
   time it gets there, looking along streets, and every patch of ground it actually sees with nobody in a red shirt
   on it is ruled out. The map shows what it has surveyed (fading with time), and it does not go back over ground it
   has just cleared or flip between two places.
4. **Find.** The camera video runs at 30 frames a second; the pretrained COCO **YOLO26n** model looks at every sixth
   frame (5 a second: plenty for people moving a few metres a second, and it leaves the CPU for the video). A person
   whose torso is red three times in 2.5 s is the suspect.
5. **Follow.** The drone flies 18 m behind them at 28 m, flying their velocity plus 1.1 times the gap to that spot
   each second (up to 15 m/s), so it stays at that distance rather than trailing: median 18 m, and within about 21 m
   90 % of the time, in the headless runs. It turns to face them and pitches the gimbal to keep them in the middle of
   the frame.
6. **Lost.** When they are out of sight for 3.5 s (a carport, an awning, a bus shelter, the garage, the park's trees,
   behind a truck or a kiosk), the drone hovers over where they were last seen and turns full circles, camera 50° down
   (from 30 m that sees 10 to 46 m out all round every 6 s), until they are back in view; it stops turning to face
   anything red it just saw.
7. **Report.** Every 2 s while it has them: *suspect at base +182.4 m E, +131.0 m N*. No GPS: the drone's own position
   is dead reckoning from the pad (its velocity and compass added up since take-off) and the camera adds the offset to
   the person (altitude, gimbal pitch, heading, where they are in the image).
8. **Police.** The patrol car leaves the station at the first report and drives the streets to the reported position,
   about 20 s away. From then on it pings its own position (its GPS: position, heading, speed) to the drone every
   second, and every report becomes a callout relative to the car: *suspect 46 m NE of you (your 2 o'clock), near Oak
   St & Main St, running N*. The car re-routes on every callout to where the suspect will be when it gets there. The
   suspect runs from the car (turning away at corners, turning round when it is ahead, hiding when it is close), so
   the chase goes on: nothing ends the run when the police get there.
9. **Voice.** The page reads the callouts out with ElevenLabs text-to-speech, through the backend's own client
   (`backend/overwatch/voice/tts.py`: the key in the repo's `.env` stays on the server; audio is cached on disk). The
   newest callout is said when the last one has finished, urgent ones first (found, lost, police on scene, back in
   view), routine ones about every 8 s; the browser's own voice is used if ElevenLabs is not available. The server
   only speaks the current run's callouts, one synthesis at a time.
10. **Views.** The video shows the drone's camera, a third-person view (3 m behind and above the drone, which is drawn
    to scale) or a bird's-eye view (95 m up, north up like the map), with the drone's camera as a live inset in the
    other two. Those two are simulator cameras for watching; the drone only ever uses its own. One view at a time,
    the same for everyone watching.

## What is real and what is simulated

Everything is simulated: the map, the people, the aircraft, the camera. Conditions are perfect on purpose (no wind,
no sensor noise, a clean camera), so dead reckoning has no drift. What the drone does is computed only from what its
sensors give it (camera pixels, velocity, compass, barometer, gimbal angle); the true positions are used by the
people (to see where the drone physically is), the renderer, and the scoring.

* People are found by pretrained YOLO26n on the rendered frames (no training on simulator images). Measured on these
  frames: 60 to 85 % of visible people found per pass out to 55 m, the red-shirt check right 160 times in 162.
* Pretrained YOLO does not see the rendered patrol car as a car (it guesses skateboard, airplane or suitcase), so the
  car is found by a classical colour detector instead: a red and a blue light-bar blob side by side on a white roof
  with black panels round it. The page says so. The first time the camera sees it, the drone checks where it puts the
  car against the car's own ping.
* Hiding is real geometry: people and the car are drawn with a per-pixel depth test against roofs, walls, trucks and
  canopies, so a hidden person has no pixels, and the search's line-of-sight model uses the same map.

## Files

| File | What |
|---|---|
| `world.py` | the map: the pursuit world's streets plus the police station, downtown buildings, a parking garage, a park, street-tree rows, carports, awnings, a covered walkway, bus shelters, kiosks and box trucks; hiding spots as map data |
| `render.py` | the camera renderer with the depth test; the patrol car and the drone (to scale) |
| `views.py` | the third-person and bird's-eye cameras |
| `people.py` | the suspect (random route, hides when chased, runs from the police car) and bystanders |
| `drone.py` | the Mini 2 flight model and the dead-reckoning navigator |
| `vision.py` | YOLO26n, the red-shirt check, the light-bar detector, pixel to ground |
| `streets.py`, `los.py` | the street network; line of sight from map geometry |
| `search.py` | the belief, the survey log and the planner |
| `follow.py` | the position filter, the match rule, follow, the 360° scan and search flight |
| `police.py` | the patrol car and its position ping |
| `sim.py` | one run, one physics step per video frame; reports and callouts, events, the summary |
| `hud.py`, `frames.py` | the camera overlay; the worker processes that draw the 30 fps video |
| `live.py` | the live run's own process (the run, YOLO, the frame workers), so the web server only relays frames |
| `server.py`, `web/` | the API and the page (mounted by `backend/overwatch/main.py`); plays the video out at an even 30 fps; speaks the callouts |
| `run_headless.py` | many seeds without the page, scored: `backend/.venv/bin/python test12/run_headless.py --seeds 1-20 --time-limit 240` |
