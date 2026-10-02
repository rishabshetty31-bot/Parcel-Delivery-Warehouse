# Setup and run

**SYNTHETIC — FOR LEARNING ONLY.** Run these commands from the project root in Windows PowerShell. They target only the SQL Server database named in your explicit private config. `setup`, `load`, Silver `run`, and Gold `publish` write to that database; `probe`, Gold `validate`, and analysis read it. This GitHub ZIP contains CSVs and code, **not** an already populated database.

## 1. Create a local environment and private config

Prerequisites: Python 3.12, SQL Server, ODBC Driver 18 for SQL Server, Windows integrated authentication, and permission to create a new local development database. The reviewed same-computer replay used `pyodbc==5.2.0`.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-step5.txt
$configPath = Join-Path $env:TEMP ('parcel-runtime-' + [guid]::NewGuid().ToString('N') + '.json')
Copy-Item config\bronze_runtime.template.json $configPath
# Edit $configPath: replace the server and database placeholders.
& .\.venv\Scripts\python.exe tools\bronze_pipeline.py probe --config $configPath
```

Choose a **unique new database name** using letters, digits, and underscores, beginning with a letter. The template's fixed cutoff and export timestamps match the included data. Keep the config outside the project and pass the **same** `--config` path to every SQL command. If the probe reports that your name already exists, choose another before setup. A local self-signed SQL Server instance may need a deliberately reviewed local-only `trust_server_certificate=true`; the template defaults to certificate verification. No password belongs in the repository.

## 2. Create the schemas and views

These setup commands create missing objects without dropping an existing database or overwriting historical runs. They display the selected server/database and check `DB_NAME()` on connection.

```powershell
& .\.venv\Scripts\python.exe tools\bronze_pipeline.py setup --config $configPath
& .\.venv\Scripts\python.exe tools\silver_reference.py setup --config $configPath
& .\.venv\Scripts\python.exe tools\silver_shipments_events.py setup --config $configPath
& .\.venv\Scripts\python.exe tools\silver_analytics.py setup --config $configPath
& .\.venv\Scripts\python.exe tools\gold_pipeline.py setup --config $configPath
```

Stop on any nonzero exit before continuing. The five Gold views are empty before the first publication.

## 3. Load and process one six-file dataset

`datasets/demo` is the controlled-defect demonstration; `datasets/clean` is the conforming baseline. Both have exactly six business CSVs. Bronze prints a new `run_id`; use it for all later stages. The Silver stages must run in the order shown.

```powershell
$load = & .\.venv\Scripts\python.exe tools\bronze_pipeline.py load --config $configPath --input-root datasets\demo | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $load.status -ne 'BRONZE_COMPLETE') { throw 'Bronze load failed' }
$runId = $load.run_id
& .\.venv\Scripts\python.exe tools\silver_reference.py run --config $configPath --run-id $runId
if ($LASTEXITCODE -ne 0) { throw 'Reference Silver failed' }
& .\.venv\Scripts\python.exe tools\silver_shipments_events.py run --config $configPath --run-id $runId --query-timeout-seconds 180
if ($LASTEXITCODE -ne 0) { throw 'Shipment/event Silver failed' }
& .\.venv\Scripts\python.exe tools\silver_analytics.py run --config $configPath --run-id $runId
if ($LASTEXITCODE -ne 0) { throw 'Analytical Silver failed' }
```

For the 14-shipment known-answer case, use `--input-root tests\fixtures\known_answer` in a **new** Bronze load; never mix its files with demo or clean. A completed stage rejects repetition, while a failed stage rolls back its data and can be retried on the same complete Bronze run. Correct a failed Bronze file and start a new load rather than altering its audit record.

## 4. Validate, publish, and read

Candidate validation checks prerequisites, required quality checks, keys, relationships, reporting scope, and metric invariants **before** any pointer change. Publication rechecks them inside a guarded transaction. It rejects an already-active repeat and leaves the previous publication intact on rejection or rollback.

```powershell
& .\.venv\Scripts\python.exe tools\gold_pipeline.py validate --config $configPath --run-id $runId
if ($LASTEXITCODE -ne 0) { throw 'Gold candidate validation failed' }
& .\.venv\Scripts\python.exe tools\gold_pipeline.py publish --config $configPath --run-id $runId
if ($LASTEXITCODE -ne 0) { throw 'Publication failed' }
& .\.venv\Scripts\python.exe tools\run_analysis.py --config $configPath --query 01
& .\.venv\Scripts\python.exe tools\run_analysis.py --config $configPath --query all --limit 5
```

The analysis runner reads only the active published run and prints JSON. Query 10 has a total row and category rows; use `is_total` so they are not added twice. The reviewed demo has **9,795** fact rows, **9,285** handed over, **8,975** due, and **6,964/8,975** confirmed on time. These are synthetic known answers for the fixed T/E configuration; see [VALIDATION.md](VALIDATION.md) and [METRIC_CATALOG.md](METRIC_CATALOG.md).

## Data generation and testing

The included clean/demo CSVs and known-answer fixture are ready to load. The public ZIP omits the **pre-generated** edge-case/malformed CSV folders. To run the complete data validator, first regenerate those artifacts in a **temporary project copy** so the approved included files stay untouched:

```powershell
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$testCopy = Join-Path $env:TEMP ('parcel-data-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testCopy | Out-Null
Copy-Item -Path config,tools,tests -Destination $testCopy -Recurse
New-Item -ItemType Directory -Path (Join-Path $testCopy 'evidence') | Out-Null
& $python tools\generate_data.py --all --output $testCopy
Push-Location $testCopy
try { & $python tools\validate_data.py --all } finally { Pop-Location }
```

`generate_data.py --all` creates the variant and malformed cases plus independent expected manifests under that temporary root. Run the focused generator-oracle regression directly from the GitHub checkout with `& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_generator_oracle.py -v`. The full historical SQL integration harness and its logs live in the preserved comprehensive archive; this slim ZIP does not claim to rerun them automatically. [VALIDATION.md](VALIDATION.md) distinguishes past verified results from checks a new downloader must run.
