One <discovery-run-id>.html viewer and one <discovery-run-id>.json raw trace
are generated here whenever a prospecting discovery runs.

Open the HTML file directly, or use:
  GET /api/v3/prospecting/discovery-runs/<run-id>/trace/

Append ?raw=true to retrieve the machine-readable JSON through the API.

The generated files can contain scraped public website text and should be
handled according to your data-retention policy. Obvious secret fields are
redacted, and very long strings are bounded by DISCOVERY_TRACE_STRING_LIMIT.
