# Batch freezing dates

`Batch.event_timestamp` is the original stock entry time (epoch milliseconds).
`Batch.stored_at` (`storedAt` in batch API/sync snapshots) is nullable epoch
milliseconds. In a freezer location it is the freezing date. `NULL` / JSON `null`
means the freezing date is unknown, for example when previously frozen stock was
entered after its original freezing date was lost. It must not be replaced with
the entry date or the current time.

Purchases and correction increases initialize `stored_at` to their event
timestamp. Freezer-to-freezer relocations preserve the source value, including
`NULL`. When merging into a freezer, an unknown destination date remains
unknown regardless of the source location. An unknown source date also makes
freezer-to-freezer merges unknown; two known dates use the earlier date. Other
relocations use the relocation timestamp for moved stock. Non-freezer merges
retain the minimum of non-null destination and moved dates. `event_timestamp` stays
unchanged. Freezer names are recognized by `services/location_kind.py`.

## One-off correction of old freezer stock

**Make a database backup first.** Run
`scripts/mark_freezer_stock_freezing_unknown.py` against an existing database:

```sh
python3 scripts/mark_freezer_stock_freezing_unknown.py /data/inventra.db --dry-run --entered-before 2026-10-05T00:00:00+00:00
python3 scripts/mark_freezer_stock_freezing_unknown.py /data/inventra.db --confirm --entered-before 2026-10-05T00:00:00+00:00
```

Choose the cutoff deliberately: only positive remaining stock in freezer
locations with both `event_timestamp` and non-null `stored_at` strictly before
that instant is changed (both comparisons use the same epoch-ms cutoff). The required ISO datetime must include a timezone offset.
Stock entered or frozen at or after the cutoff, fridge stock and already
unknown dates remain unchanged. Relocated batches are included: after re-entry
and a later freezer relocation, the relocation time may not be the real freezing
date. The operator decides whether this correction is appropriate. Dry-run opens
a read-only connection and reports both event time and stored_at, plus a
`relocated` column (`stored_at != event_timestamp`) for each matching batch and totals. Confirm serializes the transaction with
`BEGIN IMMEDIATE`, sets only the matching dates to `NULL`, and appends one Batch
UPDATE per changed batch at one revision so devices receive `storedAt: null`
through sync. Foreign keys are checked before commit; no schema is created or
migrated. A repeat run with no matches prints `nothing to do`, exits successfully,
and consumes no revision.
