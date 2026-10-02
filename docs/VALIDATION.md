# Validation and scope

**SYNTHETIC — FOR LEARNING ONLY.** The comprehensive project previously completed a fresh fixture, clean, and demonstration replay through Bronze, all Silver stages, Gold validation/publication, and twelve read-only analyses in a new SQL Server database. The replay used a fresh Python environment and an explicitly selected database **on the same computer**. It did not test installation on a different machine or production use. This GitHub edition contains the same core executable files and approved clean/demo/known-answer sources, but no populated database or historical command logs.

## Independently reviewed outcomes

| Check | Known-answer fixture | Clean | Demonstration |
| --- | ---: | ---: | ---: |
| In-scope Gold shipment facts | 14 | 9,800 | 9,795 |
| Handed over | 12 | 9,300 | 9,285 |
| Due by cutoff | 10 | 9,000 | 8,975 |
| Confirmed on time | 4 / 10 | 7,000 / 9,000 | 6,964 / 8,975 |
| First-attempt success | 4 / 7 | 6,372 / 8,525 | 6,335 / 8,484 |
| Tracking coverage | 10 / 12 | 9,200 / 9,300 | 9,185 / 9,285 |

The fixture has **14 tracking messages** but **10 physical attempts**. Its six transit-eligible shipments total **410,400 exact seconds**; two open overdue shipments total **172,800 exact seconds**. The demonstration has **650** open overdue shipments and **11** due outcomes held as Unknown. Gold stores per-shipment hours to six decimals; exact duration reconciliation uses integer seconds in Silver. For denominators and exclusions, use [METRIC_CATALOG.md](METRIC_CATALOG.md), not rounded percentages in a screenshot.

The same-computer SQL verification checked source-row dispositions, key/relationship uniqueness, certificate windows, cutoff boundaries, zero-attempt certificates, all eight Gold dimension roles, unknown members, required quality checks, publication rejection/rollback, and all twelve analysis queries. It left the original local publication unchanged. The public ZIP does **not** include the underlying historical database, raw test logs, or pre-generated edge-case folders; those remain in the separate comprehensive handover.

## What this smaller ZIP was checked for

The GitHub edition was checked independently of the comprehensive folder. All **51** included operational code/SQL/configuration/data/expectation files matched the verified source byte for byte. Its focused generator-oracle regression passed **3/3 tests**. All **13** included Python files parsed, and the eight documented generator/validator/stage/analysis entry points opened their CLI help successfully without database work.

In a separate temporary project copy, `generate_data.py --all --output <copy>` recreated **342 variant CSVs** and **29 malformed-case CSVs**. `validate_data.py --all` then passed. The **21** approved clean/demo/known-answer CSV and main expected-manifest hashes matched the included files after regeneration. The omitted corpus can therefore be restored for deeper data tests without bundling hundreds of pre-generated files in the public GitHub tree. That regeneration did **not** load SQL or switch a publication pointer.

Documentation checks found **146** resolving relative links, including **9** image embeds, with zero broken targets. The five PNG diagrams decode as images and keep editable Mermaid sources. The package scan found no private runtime configuration, high-confidence secret pattern, or personal machine path in the public files. The new ZIP was extracted and compared file by file; the external checksum file is beside the ZIP, not inside it. The older comprehensive package remains available separately for the full SQL regression harness and historical evidence.

To repeat source generation and the full data validator, follow the temporary-copy steps in [SETUP_AND_RUN.md](SETUP_AND_RUN.md). The included [known-answer CSVs](../tests/fixtures/known_answer/) and [independent expectations](../tests/expected/) are test inputs only; warehouse transformations consume the six selected business CSVs and run configuration, never expected-result files. A new SQL replay requires your own local server and a unique database name.

<a id="reference-benchmark-and-scope"></a>

## Reference benchmark and originality

The original reference warehouse and its audit informed the technical depth and presentation style. They are **not** dependencies or business sources in this GitHub edition. This project uses original parcel-domain rules and fictional geography; it does not copy reference authorship, retail findings, or financial measures. The approved parcel specification and source contracts take precedence where scope differs.
