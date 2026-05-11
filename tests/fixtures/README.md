# Test fixtures

## `data-fresh/`

A skeleton `/data` directory representing the state right after a hermes-station container's first boot against an empty volume. Used by the compat test to verify that the contract holds for a brand-new deploy. Populated by `tests/conftest.py::fake_data_dir` programmatically rather than committed.

## `data-realistic/` (gitignored)

A **sanitized snapshot of a real Railway `/data` volume**, used to verify the contract against real-world state (long-running sessions, populated memories, gateway state, etc.). This is not committed — populate it locally by:

1. Snapshot your live Railway volume: `railway run --service hermes-all-in-one tar -czf /tmp/data.tgz -C / data` (or scp from your running container)
2. Extract into `tests/fixtures/data-realistic/`
3. **Sanitize** before running tests:
   - `.hermes/.env` — replace real keys with `<PLACEHOLDER>` values
   - `.hermes/pairing/*.json` — keep structure, scrub real user IDs
   - `.hermes/state.db` — keep schema, but consider dumping/reloading without conversation content
   - `webui/.signing_key` — replace with a random 32-byte file (the test only checks it persists, not its value)
   - `webui/sessions/` — clear or scrub
4. Add a `MANIFEST.txt` listing what was scrubbed so the next person remembers.

The compat test detects this directory's presence and runs the realistic-fixture suite only if it exists.
