# Demo runbook

A five-minute walkthrough of the guaranteed demo, then the four worlds. Everything shown is SIMULATED and labelled
so. No hardware and no keys are needed.

## Before the demo (once)

```bash
make setup
```

```bash
cd dashboard && npm run build
```

Start the backend. It serves the built dashboard on http://localhost:8000:

```bash
make backend
```

During development, `make dashboard` serves the dashboard with hot reload on http://localhost:5188. Optional: add
`OPENAI_API_KEY` to `.env` for the real decisions engine. Without a key the header reads "rules only (no API key)",
or shows the labelled MOCK engine in the simulator.

Check that everything works end to end. The script runs the demo below twice in headless Chrome and compares the
outcomes:

```bash
cd backend && .venv/bin/python scripts/e2e_demo.py
```

## The quick demo (any simulation)

Open http://localhost:8000. The left column lists every simulation by world; click one to load it. **Start here**
loads PP0: a volunteer jogs round two corners and the drone follows on its own. The right column says what the drone
is doing ("Following the designated person", "Holding: the camera re-bound to someone nobody has confirmed…"), the
last decision, the map and the latest events. Play, pause and restart are the only other controls. **Full dashboard**
(top right, or http://localhost:8000/dashboard) has every control.

Three things to try on the demo page:

1. **Dispatch (PP8, or SR4 in the woods).** The panel is prefilled with "red shirt, jeans" and where the person was
   last seen; the scenario dispatches by itself after 2 s, or press **Dispatch** with your own words. Watch "Now
   playing": taking off, flying to the search area, search leg n of 8, match found. Every person the detector sees
   is logged ("Person seen (blue top, …): does not match"). Once tracking, the position line reads "142 m from the
   launchpad, bearing 075° (E), moving at 1.8 m/s" with lat/lon. **Wrong person** rejects the match: the drone goes
   back to its legs and never picks that track again.
2. **Drive a person.** In **People (simulator)** pick anyone, then **Send to a map point** and click the map, or
   click the panel and hold the arrow keys. The drone keeps following whoever it is tracking, so you can lead it
   under the carports or round a corner.
3. **A police chase with a phone (CT1).** Load **City chase** → CT1. The drone sits on the base's pad; after 2 s the
   scripted operator dispatches it for "red shirt, jeans" and it launches, climbs to 45 m, finds the person and
   follows. In **Drive the target from a phone** press **Open a tunnel and show the QR code**: the server starts a
   Cloudflare quick tunnel (needs `cloudflared`; about 30 s) and shows a QR code. Scan it with a phone: the page is
   just a joystick and a speed; push the stick and the target walks that way, the drone follows, and the towers hide
   them at every corner. (`make tunnel` does the same from a terminal.) CT2 is the same chase scripted: the target
   cuts behind the towers at each corner.
4. **The jev-drone stack (PJ1, PJ2).** The MuJoCo Skydio X2 follows the runner at 7 m. "Now playing" shows the
   reflex when it brakes or steers for a street tree, the committed D6 maneuver, and the free path ahead from the
   range sectors.

## test12: police drone dispatch (http://localhost:8000/test12)

A self-contained page (`test12/` in the repo root; restart the backend after pulling it). Click the map to choose
where the suspect starts (or leave it random), then press **Start simulation**: a 911 call reports a person in a red
shirt running from a street corner near the police station; the DJI Mini 2 stand-in climbs from the station's pad,
searches where they could be by now (the map shows the guesses, the ground it has surveyed and where it is going),
finds them with YOLO26n plus a shirt-colour check, follows from behind and reports "base +X m E, +Y m N" by dead
reckoning. The police car leaves at the first report and pings its position every second; from then on each callout
says where the suspect is from the car ("46 m north-east of you, your 2 o'clock"), read out with ElevenLabs. When the
suspect hides, the drone hovers where they were last seen and turns full circles until they are back in view. Nothing ends the run when the
police get there: it goes on until **Stop** (or half an hour). Switch the video between the drone's
camera, a third-person view and a bird's-eye view (simulator cameras, with the drone's camera as an inset). Every run
is a new random seed (shown; type it in to replay one). The first start takes about 10 s (the map and YOLO load).
While it runs, the main simulation is paused. Details: [test12/README.md](test12/README.md).

## The guaranteed demo (training area A)

1. **Start.** Open http://localhost:8000/dashboard and click **Start demo incident**. The header shows the incident id and
   SIMULATED.
2. **Search.** Within seconds, **Review queue** lists "possible person" candidates. Each has its evidence frame,
   provenance, position uncertainty and a stale tag when it ages. Point out the footer: "no detection does not mean
   the area is clear".
3. **Review.** Click a candidate to see its evidence and location, then **Confirm**. **Recommendations** now shows
   "Notify responder", proposed by the rules and waiting for approval.
4. **Responder.** On a phone (or a phone-sized browser window), open http://<host>:8000/responder, tap **Enable
   voice** and choose **Use the simulated responder position**. Back on the dashboard, **Approve** the
   recommendation. The phone flashes, vibrates where supported and speaks the brief ("…, 11 o'clock, about 18
   metres, plus or minus 3"). Tap **ACKNOWLEDGE**; the dashboard's alert turns acknowledged.
5. **Structure.** Switch the mission to **Structure**. The building's openings are proposed (a simulator fixture,
   labelled). Set **Side A faces** to south: openings group into sides A to D. Tag the garage as **blocked
   entrance** on side D.
6. **3D and replay.** Open the **3D world** tab. Then, in the bottom panel, **Replay timeline** steps through the
   incident log. Show **Metrics** (hover a value for its source) and the **Audit log**.

## Follow (police pursuit world)

Choose **PP3** under Police pursuit; a new incident starts in Follow mode. Click the red-shirted volunteer and
**Designate target**. The Follow panel shows the trail, "designated by operator" and motion statistics only from
verified positions. When the look-alike crosses, the target is never re-assigned without a person. OW4 in the
training area shows the lock re-binding and the panel saying "unverified: confirm or re-designate".

## Search and rescue, flood, fire

- **SR1 or SR2:** the pilot flies a lawnmower sweep (scripted manual flight). The lost person is flagged; in SR2 the
  dropped jacket and the tarp are flagged too, which is honest: the camera cannot tell them apart.
- **FR1:** water rises over the southern yards (screening model, time compressed). The map shows the water layer;
  Search flags the people waiting in the water. **Sim thermal** shows the labelled simulated thermal inset.
- **FF2:** a grass fire spreads towards the house. The map shows burning, keep-out and smoke areas; openings under
  smoke read "unclear". No output can say an entry is safe.

## Indoor and scenes

Switch to **Indoor**. The **Scene** tab opens. Load **Sample indoor scan** (synthetic, labelled). Add a room
label by clicking the scan; the incident's observations are not drawn until you save an alignment, and the viewer
says which frame is missing.

## If something goes wrong

- **No video:** the source panel says why (STALE, OFFLINE or NOT_CONFIGURED); the simulator restarts with the
  scenario chip.
- **No candidates in Search:** the drone may be looking away; restart the scenario (↻) or pick DEMO-1 again.
- **Responder shows nothing:** refresh it after the incident has started, or pick the incident from its list.
- **Backend restarted mid-demo:** the incident reloads with its scenario and its log; the dashboard reconnects.
