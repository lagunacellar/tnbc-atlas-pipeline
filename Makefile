# TNBC Atlas — pipeline operations
#
# Convenience targets for the full pipeline. Database connection is resolved
# from these env vars, in priority order:
#   1. DATABASE_URL              — full Postgres URI (Supabase production)
#   2. PGHOST/PGUSER/PGDATABASE  — classic libpq variables
#   3. /tmp/pgsock socket        — sandbox-pilot default
# Python 3.10+ with deps from requirements.txt installed.
#
# Quick start (first time):
#   make install      # installs Python deps
#   make db-init      # creates schema in your Postgres
#   make harvest      # runs the three primary harvesters
#   make dedup        # dedup + load to Postgres
#   make enrich       # Crossref + Unpaywall + retraction sweep
#   make filter       # OpenAlex post-filter
#   make tag          # topic-tagging pass
#   make tier2        # tier-2 algorithmic nomination
#   make report       # exports + coverage report + browser
#
# Or all-at-once:
#   make all

PYTHON ?= python3

# Sandbox-pilot defaults; production overrides via DATABASE_URL env var
PGHOST ?= /tmp/pgsock
PGUSER ?= postgres
PGDATABASE ?= tnbc_atlas

export PGHOST PGUSER PGDATABASE DATABASE_URL CONTACT_EMAIL

.PHONY: help install db-init harvest harvest-pubmed harvest-europepmc harvest-openalex \
        dedup enrich enrich-crossref enrich-unpaywall sweep-retractions \
        filter tag tier2 benchmark report browser all clean

help:
	@echo "TNBC Atlas pipeline. Common targets:"
	@echo "  make install        # pip install requirements"
	@echo "  make db-init        # apply schema"
	@echo "  make harvest        # PubMed + Europe PMC + OpenAlex"
	@echo "  make dedup          # dedup + load to Postgres"
	@echo "  make enrich         # Crossref + Unpaywall + retraction sweep"
	@echo "  make filter         # OpenAlex stricter post-filter"
	@echo "  make tag            # rule-based topic tagging"
	@echo "  make tier2          # tier-2 algorithmic nomination"
	@echo "  make benchmark      # tier-1 recall benchmark"
	@echo "  make report         # exports + coverage report + browser HTML"
	@echo "  make all            # full pipeline end-to-end"

install:
	$(PYTHON) -m pip install -r requirements.txt

db-init:
	@if [ -n "$$DATABASE_URL" ]; then \
		psql "$$DATABASE_URL" -f sql/01_schema.sql; \
		psql "$$DATABASE_URL" -f sql/02_enrichment_migration.sql; \
		psql "$$DATABASE_URL" -f sql/02b_quality_passes_migration.sql; \
		psql "$$DATABASE_URL" -f sql/03_supabase_public_api.sql; \
	else \
		psql -d $(PGDATABASE) -f sql/01_schema.sql; \
		psql -d $(PGDATABASE) -f sql/02_enrichment_migration.sql; \
		psql -d $(PGDATABASE) -f sql/02b_quality_passes_migration.sql; \
		psql -d $(PGDATABASE) -f sql/03_supabase_public_api.sql; \
	fi

harvest: harvest-pubmed harvest-europepmc harvest-openalex

harvest-pubmed:
	$(PYTHON) scripts/harvest_pubmed.py

harvest-europepmc:
	$(PYTHON) scripts/harvest_europepmc.py

harvest-openalex:
	$(PYTHON) scripts/harvest_openalex.py

dedup:
	$(PYTHON) scripts/dedup_and_load.py

enrich: enrich-crossref enrich-unpaywall sweep-retractions

enrich-crossref:
	$(PYTHON) scripts/enrich_crossref.py

enrich-unpaywall:
	$(PYTHON) scripts/enrich_unpaywall.py

sweep-retractions:
	$(PYTHON) scripts/retraction_sweep.py

filter:
	$(PYTHON) scripts/filter_openalex_only.py

tag:
	$(PYTHON) scripts/tag_topics.py

tier2:
	$(PYTHON) scripts/nominate_tier2.py

benchmark:
	$(PYTHON) scripts/tier1_benchmark.py

report:
	$(PYTHON) scripts/export_and_report.py
	$(PYTHON) scripts/build_browser.py

all: harvest dedup enrich filter tag tier2 benchmark report

# Clean working data (preserves committed code, schema, curation)
clean:
	rm -rf snapshots/*/*.jsonl snapshots/*/*.txt
	rm -f exports/*.csv exports/*.jsonl exports/*.bib exports/*.ris
	rm -f reports/*.csv reports/retracted.csv
	rm -f bibliography_browser.html
	rm -f logs/*.log logs/*.out
