# Plan: Django ORM and Database Optimisation (TVMaze + PostgreSQL)

## Environment found
- Python 3.13.1, Windows 11
- PostgreSQL 18 service is running (needs a password for login)
- Django 5.2 LTS, psycopg 3, requests, matplotlib, python-dotenv

## Batches
1. Setup: Django project `config`, app `shows`, `.env.example`, `requirements.txt`, `.gitignore`, PostgreSQL settings.
2. Models (Network, Show, Episode) and the first migration (no index on `Show.show_type`).
3. `import_tvmaze` command: fetch shows and episodes, use TVMaze IDs with `update_or_create` so re-runs make no duplicates.
4. `benchmark_queries` command: N+1 experiments A, B, C, then the index experiment (before/after a second migration), CSV output, graphs.
5. Tests (relationships, missing network, no duplicates, query counts, CSV sanity).
6. Run everything for real, read the results, write the README from the real numbers.

## Key decisions
- Index experiment: `Show.show_type`/`status` is low selectivity, so use a more selective field if the data supports it. Report honestly if PostgreSQL ignores the index.
- Dataset: a few hundred shows, episodes for a limited subset. Size is documented in the README.
- No made-up numbers. All results come from real runs.
