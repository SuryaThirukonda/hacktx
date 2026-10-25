# Overwatch developer commands
PY := backend/.venv/bin/python

.PHONY: setup backend backend-live replay devices dashboard build test eval eval-quick markers schemas bench-models bench-small bench-decisions bench-incident e2e tunnel clean setup-drone drone-live

setup:            ## create the Python venv and install backend + dashboard deps
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd dashboard && npm install

backend:          ## run the backend (SIMULATED mode by default) on :8000
	cd backend && .venv/bin/python -m uvicorn overwatch.main:app --host 0.0.0.0 --port 8000

backend-live:     ## run the backend against real devices (bridge + phone sockets, or OVERWATCH_DRONE_URL)
	cd backend && OVERWATCH_MODE=LIVE .venv/bin/python -m uvicorn overwatch.main:app --host 0.0.0.0 --port 8000

replay:           ## replay a recorded session through the same pipeline: make replay SESSION=sessions/<name>
	cd backend && OVERWATCH_MODE=REPLAY OVERWATCH_REPLAY_PATH=../$(SESSION) .venv/bin/python -m uvicorn overwatch.main:app --host 0.0.0.0 --port 8000

devices:          ## fake DJI bridge + responder phone over the real protocols (backend must be in LIVE mode)
	cd backend && .venv/bin/python scripts/fake_devices.py --operator

setup-drone:      ## live vision, video, optional voice and simulator/test dependencies
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,vision,h264,voice,sim3d]"

drone-live:       ## LIVE video/ML + OpenAI Decisions; existing JPEG relay supported on :8765
	cd backend && .venv/bin/python scripts/run_drone_live.py

dashboard:        ## run the dashboard dev server on :5188 (proxies /api and /ws to :8000)
	cd dashboard && npx vite --host 0.0.0.0 --port 5188

build:            ## build the dashboard; the backend then serves it at http://<mac>:8000/
	cd dashboard && npm run build

test:             ## backend unit + integration tests and dashboard typecheck
	cd backend && .venv/bin/python -m pytest -q
	cd dashboard && npx tsc --noEmit -p tsconfig.json

eval:             ## run every simulator scenario headless and write evaluation/reports/<ts>/
	cd backend && .venv/bin/python scripts/run_scenarios.py

tunnel:           ## Cloudflare quick tunnel to the backend: the phone controller at <url>/drive
	./scripts/tunnel.sh 8000

eval-quick:       ## demo + Overwatch scenarios only
	cd backend && .venv/bin/python scripts/run_scenarios.py --quick demo OW1 OW4 S05

markers:          ## printable ArUco markers for the field (PNG + PDF in evaluation/markers)
	cd backend && .venv/bin/python scripts/make_markers.py

schemas:          ## export JSON Schemas of every wire contract to schemas/
	cd backend && .venv/bin/python scripts/export_schemas.py

bench-models:     ## detector benchmark on synthetic frames (pass SRC=video.mp4 for real footage)
	cd backend && .venv/bin/python scripts/benchmark_models.py $(if $(SRC),--source $(SRC),--synthetic demo)

bench-small:      ## small overhead targets (S09 climb): plain vs tiled YOLO, recall by target size
	cd backend && .venv/bin/python scripts/benchmark_models.py --synthetic S09 --stride 5 --iou 0.3 --imgsz 960 \
		--models sim_blob,sim_blob+tiles,yolo26n,yolo26n+tiles,yolo26s,yolo26s+tiles
	cd backend && .venv/bin/python scripts/benchmark_models.py --composite --frames 30 --iou 0.3 --imgsz 960 \
		--models yolo26n,yolo26n+tiles,yolo26s,yolo26s+tiles

bench-decisions:  ## decision-engine benchmark (engines without keys report NOT RUN)
	cd backend && .venv/bin/python scripts/benchmark_decisions.py --label-sensitivity

e2e:              ## the demo end to end in headless Chrome, twice, outcomes compared (needs a dashboard build)
	cd backend && .venv/bin/python scripts/e2e_demo.py

bench-incident:   ## SIMULATED incident benchmark: demo world, scripted operator and responder, metrics from the log
	cd backend && .venv/bin/python scripts/benchmark_incident.py

.PHONY: sar-demo sar-eval rescue-eval e2e-workspace e2e-live e2e-sar
sar-demo:         ## deterministic SAR review/ACK demo, loopback :8011
	PYTHONPATH=backend $(PY) backend/scripts/run_sar_demo.py --port 8011

sar-eval:         ## rendered SAR1 acceptance through incident API and ACK
	PYTHONPATH=backend OVERWATCH_DB=:memory: $(PY) backend/scripts/evaluate_sar.py --output evaluation/current/sar-acceptance

rescue-eval:      ## flood/fire/forest launch, beacon, human review, crew and return
	PYTHONPATH=backend $(PY) backend/scripts/evaluate_rescue.py --output evaluation/current/rescue

e2e-workspace:    ## redesigned workspace, City handoff, coverage and review
	PYTHONPATH=backend $(PY) backend/scripts/e2e_workspace.py

e2e-live:         ## LIVE UI and disarmed startup without hardware
	PYTHONPATH=backend $(PY) backend/scripts/e2e_live_dashboard.py

e2e-sar:          ## incident-command SAR review and responder ACK
	PYTHONPATH=backend $(PY) backend/scripts/e2e_sar.py
