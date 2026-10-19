# Public GitHub candidate metadata scans

This optional, standalone scanner fetches public repository metadata for existing
discovery candidates and precise GUI/browser automation topics. It is not yet a
phase of `scripts/scan.py` or a scheduled workflow. Python 3.11+ and the standard
library are sufficient; it does not read credentials or require an account.

```sh
# One bounded pilot: at most ten actual, unauthenticated HTTP GETs.
python3 scripts/scan_github_candidates.py \
  --output .cache/github-candidate-scan \
  --max-requests 10 --max-seconds 180 --existing-budget 5 \
  --topic computer-use --topic browser-automation \
  --created-before 2026-10-18

# Resume the same epoch. Omitted topics and dates keep the frozen configuration.
python3 scripts/scan_github_candidates.py \
  --output .cache/github-candidate-scan \
  --max-requests 10 --max-seconds 180 --existing-budget 5
```

The first command's date is an example; choose the intended UTC cutoff when
starting a new epoch. `--created-after` defaults to `2008-01-01`.
`--created-before` defaults to the first run's UTC date and then remains frozen.
Changing the topic set or bounds requires a new output directory. The CLI only
writes beneath this checkout's ignored `.cache/` directory and refuses parent
traversal and symlink paths.

`candidate-metadata.json` is the atomic state and observation ledger. Its schema
is [github-candidate-scan.schema.json](../schemas/github-candidate-scan.schema.json).
It contains input fingerprints, repository records, query shards, request
receipts, run summaries and resume cursors. A repository's official numeric ID
is the counting key. Former public names and repeated query occurrences remain
as provenance, without increasing the unique count. A paper, model, dataset or
separately reviewed monorepo component is not inferred from a repository hit.

Every record has `metadata_status: primary_metadata_observed`,
`review_status: candidate` and `scope: relevance_pending`. It is not an active
catalog record. Descriptions and tags are publisher metadata, not independently
verified capabilities. No license, release availability, paper relation or
editorial approval is inferred. This scanner does not edit `data/`, rebuild the
catalog, publish files, or execute downloaded code.

## Inputs and priorities

Each run records exact SHA-256 fingerprints for `catalog.jsonl`,
`candidates.jsonl`, `primary-evidence.jsonl`, `repository-aliases.json` and
`candidate-reviews.json`, as well as the active and curated counts actually read.
Do not reuse a different checkout's counts or fingerprint when reporting a run.

Existing GitHub candidates without a successful retained primary response are
considered first, ordered by distinct discovery-source count and stable name.
Known repository aliases resolve before requests. `--existing-budget` caps this
phase per invocation; topic shards can use the remaining request budget. Its
cursor persists. An unavailable existing candidate is recorded as an unknown
attempt and the cursor advances, so a permanent 404 cannot block the queue.
There is no automatic repeated probing of that completed seed within an epoch.
If input fingerprints change, a new seed cohort is created, unfinished old seed
cursors are marked `superseded_input`, and previously observed repository data
and search-shard progress remain intact.
Returning to a previously seen input restores that input's own cursor and
supersedes other pending seed cohorts. Its seed count must still match the
retained fingerprint and the current input; a cursor never runs against another
snapshot's candidate list.

Topic choices are constrained by the CLI. Every search uses `is:public
fork:false`, a precise topic, a fixed creation-date interval, `sort=updated` and
`order=asc`. It never performs a broad README search for “computer use.”

## Pagination and limits

A result population over 900 causes the current inclusive creation-date range
to split into two non-overlapping date ranges. Children are persisted immediately
and processed within later budget slots. Metadata already fetched from a parent
remains auditable and deduplicates against its children. A single-day range that
still exceeds the threshold is `blocked_single_day_window`: it needs a reviewed
additional partition strategy, and is not described as covered.

Smaller shards use pages of at most 100 results, never beyond GitHub's ten-page
search window. Each receipt records the API-reported total, returned row count,
`incomplete_results`, exact response SHA-256 and byte count. Changed totals,
duplicate/missing identities, rejected rows, an incomplete result flag and early
empty pages prevent an `observed_pages_exhausted` result. This status means the
observed pages reconcile with the observed total; search ordering and repository
state can change between requests. It is not an immutable snapshot or a claim
of global recall. `finished` describes a run with no pending work, not complete
coverage of GUI automation.

Requests are sequential, at least 6.5 seconds apart, including immediate resume
against the same ledger. The budget counts HTTP attempts, not successful pages;
there are no automatic redirects or retries. The wall-clock budget prevents new
requests and limits each request timeout to the remaining budget (at most 20
seconds). On 429 or a rate-limit 403, the provider stops and a derived cooldown
is retained; unrequested shards remain pending. Other HTTP errors are explicit
unknown attempts. A failed search page keeps its cursor for a later invocation
and cannot masquerade as a prior success.
Standard GitHub rate-limit messages in a 403 JSON body also stop the provider
when rate-limit headers are absent. The body is not retained. The deadline is
checked again after spacing waits, before counting or starting another request.

The scanner is single-writer: do not run two processes against the same output
directory. Atomic state replacement prevents partially written JSON. A request
completed just before an interrupted state write may be fetched again on resume;
numeric-ID deduplication prevents double-counting it.

## Public-data boundary and reporting

The scanner does not read a GitHub token, send authorization/cookies, use
environment proxy credentials, or follow redirects. Each returned row must also
have literal `private=false`, `visibility=public` and `fork=false`, regardless of
the search qualifiers. Missing or contradictory flags reject the row. Canonical
repository URLs and timestamps must be valid; search results must belong to the
requested topic and creation-date range.
A direct repository response must match the requested `owner/name`, ignoring
case, because redirects are not followed. A mismatched 200 response is rejected
and neither its metadata nor raw body is retained.

Only necessary public repository fields are retained in records. Owner/contact
objects, permissions, request headers and credentials are not copied into the
ledger. Unavailable or rejected responses do not expose their unverified request
URL or response body there; receipts retain a request-URL fingerprint, response
fingerprint and reason counts. Exact response bodies are cached by SHA-256 only
when the response parses without any rejected rows. A mixed/private/unknown
response has a receipt but no retained raw body. All raw bodies remain under the
ignored output cache; do not commit them or publish them as downloads.
Resume validation checks both directions of the evidence link: each occurrence
must match its receipt's query, position and accepted repository ID, and every
accepted ID must have a record with a matching occurrence. Existing-candidate
receipts must belong to the same input snapshot as their run. Retained source
URLs must also match their request-URL fingerprints.

Report `after_unique_repositories`, `new_unique_repositories`, input fingerprint,
actual request count, unknown attempts, rejected-row reasons and deferred shards.
Query occurrences and accepted rows can overlap. Neither their sum nor
`reported_total` is the number of independently scanned resources. A large
metadata-observed candidate population still needs relevance and source review;
the scan target does not imply that all candidates belong in the curated catalog.

```sh
PYTHONPATH=tests python3 -S -B -m unittest test_github_candidate_scan -v
# With the repository validation dependency installed, also checks JSON Schema.
PYTHONPATH=tests .venv/bin/python -B -m unittest test_github_candidate_scan -v
```
