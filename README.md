# Django ORM and Database Optimisation (TVMaze API + PostgreSQL)

> Labels used in this README: **[MEASURED]** = real numbers from my runs on this laptop (they are in `results/benchmark_results.csv`). **[EXPECTED]** = what theory says should happen. **[EXAMPLE]** = an illustration, not measured.

## 1. Project overview
A small Django project that downloads TV shows and episodes from the TVMaze API into PostgreSQL, then runs experiments that show:
1. the **N+1 query problem** and how `select_related()` / `prefetch_related()` fix it;
2. what a **database index** does (and when it does not help).

## 2. Problem statement
Django's ORM makes it easy to write code that looks fine but sends hundreds of SQL queries. A page that lists 485 shows with their network can silently run 457 queries. Also, adding indexes "to make things faster" does not always work. I want to measure both effects with real numbers.

## 3. Objectives
- Import TVMaze data into PostgreSQL without duplicates.
- Show the N+1 problem, count real SQL queries, fix it, and compare.
- Run an indexing experiment using Django migrations and `EXPLAIN ANALYZE`.
- Save real results to CSV and draw graphs.

## 4. Technologies used
Python 3.13, Django 5.2 (LTS), PostgreSQL 18, psycopg 3, `requests`, `python-dotenv`, Matplotlib. Timing uses `time.perf_counter()`; query counting uses Django's `CaptureQueriesContext`.

## 5. Architecture and data flow
```
TVMaze API --(import_tvmaze, only step that uses the internet)--> PostgreSQL
                                                                      |
                              benchmark_queries (Django ORM) <--------+
                                   |
                   results/benchmark_results.csv, query_plans.txt, graph_*.png
```
After the import, every experiment only talks to PostgreSQL.

## 6. Database schema
```
Network (1) ----< (many) Show (1) ----< (many) Episode
```
| Model | Fields |
|---|---|
| Network | tvmaze_id (unique), name, country |
| Show | tvmaze_id (unique), name, show_type, language, status, official_site, **network** (ForeignKey, can be NULL) |
| Episode | tvmaze_id (unique), name, season, number (can be NULL), airdate (can be NULL), **show** (ForeignKey) |

- `Show.network` is a **ForeignKey** with `related_name="shows"`: `network.shows.all()` is the **reverse relationship**.
- `Episode.show` has `related_name="episodes"`: `show.episodes.all()`.
- Some shows have no network, so the field allows NULL (11 of the first 240 shows had none).
- `tvmaze_id` is unique, and the import uses `update_or_create(tvmaze_id=...)`, so running it twice never duplicates rows.

## 7. Installation (Windows)
```powershell
cd "C:\Users\Ritarshi Roy\Downloads\IMAP\project"
pip install -r requirements.txt
```
(Optionally create a virtual environment first: `python -m venv .venv` then `.venv\Scripts\activate`.)

## 8. PostgreSQL configuration
1. Install PostgreSQL and make sure the service is running (`Get-Service *postgres*`).
2. Create the database. `psql` is usually not on PATH, so use the full path:
   ```powershell
   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE tvmaze_db;"
   ```
3. Copy `.env.example` to `.env` and fill in your real password. `.env` is in `.gitignore`; passwords are never written in the code.
   ```
   DB_NAME=tvmaze_db
   DB_USER=postgres
   DB_PASSWORD=your_password
   DB_HOST=localhost
   DB_PORT=5432
   ```
   If you want a separate user instead of `postgres`, run `CREATE USER myuser WITH PASSWORD '...'; ALTER DATABASE tvmaze_db OWNER TO myuser;` and put it in `.env`. For the tests the user also needs the `CREATEDB` permission.

## 9. Migrate and import
```powershell
python manage.py migrate
python manage.py import_tvmaze --pages 2
```
`--pages 2` downloads TVMaze show pages 0 and 1, then each show's episodes. It waits 0.6 s between shows to respect TVMaze's rate limit, so it takes about 8-10 minutes.

**Dataset [MEASURED]: 485 shows, 81 networks, 61,658 episodes.** I re-ran the import for page 0 and the counts did not change (no duplicates).

## 10. Run the experiments
```powershell
python manage.py benchmark_queries     # all experiments, CSV, query plans, graphs (~1 minute)
python manage.py test shows            # tests
```
The benchmark command moves the database back to migration `0001` (no indexes), measures, then applies migration `0002` (adds the indexes) and measures again. When it ends, the indexes exist.

## 11. The N+1 problem
**What it means:** 1 query fetches N shows, then N more queries fetch something related for each show: 1 + N queries.

**Why it happens:** Django loads related objects *lazily*. `Show.objects.all()` only selects from `shows_show`. The first time the code reads `show.network`, Django runs a new query for that one network. A loop that reads it for every show runs it for every show.

**Why it gets worse with more data:** queries grow with the number of rows. Each query has a fixed cost (sending SQL, waiting for the answer, reading it back), so 10x more shows means roughly 10x more of that cost.

**Effect:** slow pages and a busier database.

## 12. `select_related()` vs `prefetch_related()`
- A **SQL JOIN** combines rows of two tables in one query (`shows_show JOIN shows_network ON ...`).
- **`select_related("network")`** uses a JOIN, so each show row arrives with its network columns. It fits a ForeignKey where each show has at most **one** network.
- **`prefetch_related("episodes")`** runs a *second* query for all episodes of all those shows (`WHERE show_id IN (...)`), then Django attaches them to the right shows in Python. A show has **many** episodes, so a JOIN would repeat every show row once per episode; a separate bulk query is better. That is why this experiment needs 2 queries, not 1.
- Query counts depend on the relationship; "1 query" is not always the answer.

## 13. Database indexes and trade-offs
- An **index** is a separate sorted structure (a B-tree) that stores a column's values with pointers to the table rows. It works like a book's index.
- A **sequential scan** reads every row and checks the filter. An **index scan** (or **bitmap index scan**) jumps to the matching rows.
- **Advantages:** faster filtering on selective conditions.
- **Disadvantages:** extra disk space; every INSERT/UPDATE/DELETE must also update the index, which slows writes.
- **Why PostgreSQL may ignore an index:** it estimates costs from statistics (`ANALYZE`). If a filter matches most rows, or the table is tiny, reading the whole table is cheaper, so it chooses a sequential scan.
- **Why a tiny dataset misleads:** the whole table fits in a few pages, so a full scan is already almost instant and the index cannot win. Low-selectivity columns (few distinct values) have the same problem.

In this project: `Episode.airdate` (a date range matches few rows = selective) and `Show.status` (about 91% are "Ended" = low selectivity).

## 14. Actual benchmark results [MEASURED]
Machine: my Windows laptop, PostgreSQL 18 on localhost. Each experiment ran 10 times after one unmeasured warm-up run, using a fresh queryset each time. Source: `results/benchmark_results.csv`.

### N+1 experiments (485 shows, 61,658 episodes)
| Experiment | SQL queries | Avg time per run |
|---|---|---|
| A: shows + `show.network` (N+1) | 457 | 179.7 ms |
| B: `select_related("network")` | 1 | 8.4 ms |
| C: shows + `show.episodes` (N+1) | 486 | 727.8 ms |
| D: `prefetch_related("episodes")` | 2 | 525.4 ms |

Why 457 and not 486 for A: 29 shows have no network, so Django does not query for them (1 + 456 = 457).
A and B return identical results; C and D return identical results (the command checks this and the tests check it too).

### Indexing experiments
| Experiment | Plan used | PostgreSQL execution time | Python avg time | Index size |
|---|---|---|---|---|
| E1: episodes from March 2015 (501 rows of 61,658), no index | Seq Scan | 8.504 ms | 11.65 ms | - |
| E2: same query, with index on `airdate` | Bitmap Index Scan | 0.223 ms | 4.40 ms | 704 kB |
| F1: `status="Ended"` (442 of 485 shows), no index | Seq Scan | 0.082 ms | 2.54 ms | - |
| F2: same query, with index on `status` | Seq Scan (index ignored) | 0.066 ms | 16.95 ms | 16 kB |

Planning times were about 0.06-0.09 ms in all four runs (see `results/query_plans.txt`).

## 15. Graphs and interpretation
- `results/graph_1_query_counts.png` - query counts: 457 -> 1 and 486 -> 2.
- `results/graph_2_elapsed_times.png` - elapsed times for A-D.
- `results/graph_3_index_timings.png` - filter timings before/after indexing.

**Findings**
1. `select_related` cut queries from 457 to 1 and time from about 180 ms to about 8 ms (about 21x faster here).
2. `prefetch_related` cut queries from 486 to 2, but time only fell from about 728 ms to about 525 ms (about 1.4x). Both versions still build 61,658 Python Episode objects, and that Python work now dominates. **Fewer queries does not always mean proportionally less time**; query count and time are different measurements.
3. The `airdate` index changed the plan from Seq Scan to Bitmap Index Scan, and PostgreSQL's own execution time fell from 8.5 ms to 0.22 ms. The Python-side time fell less (11.7 ms -> 4.4 ms) because it also includes building 501 objects and talking to the database.
4. The `status` index was **not used**: PostgreSQL chose a Seq Scan both times because 91% of rows match. The Python times for F1 and F2 differ (2.5 vs 17 ms) even though the plans are identical, so that difference is **noise** (e.g. one slow run among 10 raises the average), not an effect of the index. Do not read it as "the index made it slower".

## 16. Limitations
- One laptop, one run of the benchmark command. Timings change from run to run; the query counts do not.
- The dataset is small by industry standards (485 shows, 61,658 episodes). The `shows` table is so small that indexing it cannot show any benefit.
- Only 10 repeats per experiment and a mean, which is sensitive to outliers (see F2). A median would be more robust.
- Caching: Django querysets cache results (I build a fresh queryset every run), and PostgreSQL/OS caches make repeated runs faster than a first cold run. I do a warm-up run, so all numbers are "warm cache" numbers.
- `time.perf_counter()` measures all Python code between the two timer calls: Python processing, network round-trips to PostgreSQL, and object creation, not only PostgreSQL time. `EXPLAIN ANALYZE` execution time is measured inside PostgreSQL and excludes sending rows to Python; `EXPLAIN ANALYZE` also runs the query itself.
- I only ran the "write cost" of indexes in theory; I did not measure insert slowdown.
- Only one date window (March 2015) was tested.

## 17. Conclusion
The N+1 problem is real and easy to create: a plain loop over 485 shows ran 457 queries. `select_related` and `prefetch_related` removed almost all of them; the speed-up was large for the JOIN case and modest for the episodes case. An index helped only the selective date filter and was ignored for the low-selectivity status column. Always measure with real data instead of assuming.

## 18. References
- TVMaze API: https://www.tvmaze.com/api
- Django `select_related` / `prefetch_related`: https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-related
- Django model indexes: https://docs.djangoproject.com/en/5.2/ref/models/indexes/
- Django `CaptureQueriesContext`: https://docs.djangoproject.com/en/5.2/topics/testing/tools/#django.test.utils.CaptureQueriesContext
- PostgreSQL `EXPLAIN`: https://www.postgresql.org/docs/current/sql-explain.html
- PostgreSQL indexes: https://www.postgresql.org/docs/current/indexes.html

## Project files
```
manage.py, config/settings.py        Django settings (reads .env)
shows/models.py                      Network, Show, Episode (+ index definitions)
shows/migrations/0001, 0002          tables, then indexes
shows/management/commands/import_tvmaze.py     download data
shows/management/commands/benchmark_queries.py experiments, CSV, graphs
shows/tests.py                       10 tests
results/                             CSV, query plans, graphs, import logs
```
