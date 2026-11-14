.DEFAULT_GOAL := help

REGION ?= toronto
PRODUCT ?= GCOV
MATURITY ?= VALIDATED
START ?= 2026-10-21
END ?= 2026-11-08
LIMIT ?= 1
MAX_GB ?= 10
MAX_RESULTS ?= 10
MAP_DIR ?= data/map
MAP_STEP ?= 4
MAP_PORT ?= 8000
LAKE_DIR ?= data/lake-erie
LAKE_RADAR ?= data/toledo/NISAR_L3_PR_SME2_025_055_D_067_2005_DHDH_M_20260712T003329_20260712T003404_P05023_N_F_J_001.h5

DATE_ARGS = $(if $(START),--start $(START)) $(if $(END),--end $(END))

.PHONY: lake-serve lake-prep lake-verify help sync lint format format-check test check auth products search dry-run download inspect map-prep map-serve map clean

help:
	@echo "Usage: make <target> [VARIABLE=value]"
	@echo ""
	@echo "Setup:"
	@echo "  sync          Install/update dependencies (uv sync)"
	@echo ""
	@echo "Quality:"
	@echo "  lint          Run the ruff linter"
	@echo "  format        Format code with ruff"
	@echo "  format-check  Verify formatting without changes"
	@echo "  test          Run unit tests"
	@echo "  check         Run lint + format-check + test"
	@echo ""
	@echo "NISAR:"
	@echo "  auth          Verify Earthdata Login credentials"
	@echo "  products      Count products for REGION and date range"
	@echo "  search        Preview granules and write data/<region>-search.json"
	@echo "  dry-run       Show what download would fetch"
	@echo "  download      Download LIMIT granules with a MAX_GB cap"
	@echo "  inspect       Inspect GRANULE (make inspect GRANULE=NISAR_...)"
	@echo ""
	@echo "Map:"
	@echo "  lake-serve    Open Lake Erie radar, temperature and chlorophyll layers"
	@echo "  lake-prep     Prepare the three Lake Erie layers from local sources"
	@echo "  lake-verify   Compare real source/COG/API pixels for each layer"
	@echo "  map-prep      Convert the first granule into HH/HV/RGB COGs"
	@echo "  map-serve     Serve the tile API and MapLibre frontend"
	@echo "  map           Prepare and serve in one step"
	@echo ""
	@echo "Other:"
	@echo "  clean         Remove caches and build artifacts"
	@echo ""
	@echo "Variables: REGION=$(REGION) PRODUCT=$(PRODUCT) MATURITY=$(MATURITY)"
	@echo "           START=$(START) END=$(END) LIMIT=$(LIMIT) MAX_GB=$(MAX_GB)"
	@echo "           MAP_DIR=$(MAP_DIR) MAP_STEP=$(MAP_STEP) MAP_PORT=$(MAP_PORT)"

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

test:
	uv run pytest

check: lint format-check test

auth:
	uv run nisar-fetch auth

products:
	uv run nisar-fetch products --region $(REGION) --maturity $(MATURITY) $(DATE_ARGS)

search:
	uv run nisar-fetch search --region $(REGION) --product $(PRODUCT) \
		--maturity $(MATURITY) $(DATE_ARGS) \
		--max-results $(MAX_RESULTS) --json data/$(REGION)-search.json

dry-run:
	uv run nisar-fetch download --region $(REGION) --product $(PRODUCT) \
		--maturity $(MATURITY) $(DATE_ARGS) \
		--limit $(LIMIT) --max-gb $(MAX_GB) --dry-run

download:
	uv run nisar-fetch download --region $(REGION) --product $(PRODUCT) \
		--maturity $(MATURITY) $(DATE_ARGS) \
		--limit $(LIMIT) --max-gb $(MAX_GB)

inspect:
	@test -n "$(GRANULE)" || (echo "GRANULE is required: make inspect GRANULE=NISAR_..." && exit 1)
	uv run nisar-fetch inspect --granule $(GRANULE)

map-prep:
	uv run nisar-fetch map-prep --data-dir data/$(REGION) --out $(MAP_DIR) \
		--step $(MAP_STEP) --region $(REGION)

map-serve:
	uv run nisar-fetch map-serve --map-dir $(MAP_DIR) --port $(MAP_PORT)

map: map-prep map-serve

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +

# Lake observations are kept separate from the original Toronto demo.
lake-prep:
	uv run nisar-fetch lake-prep --radar "$(LAKE_RADAR)" \
		--temperature "$(LAKE_DIR)/sources/temperature-2026-07-12.nc" \
		--blooms "$(LAKE_DIR)/sources/cyanobacteria-2026-07-12.tif" --out "$(LAKE_DIR)"

lake-serve:
	uv run nisar-fetch map-serve --map-dir "$(LAKE_DIR)" --port $(MAP_PORT)

lake-verify:
	uv run python scripts/verify_lake.py
