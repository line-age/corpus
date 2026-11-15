# Fixtures

Identifier lists for real runs against Internet Archive. Identifiers only: nothing here is
copied from the scans or OCR (L-0009 keeps fixtures public and synthetic).

- `pgj-sample-15.txt`: the 15-issue Pipeline & Gas Journal sample used for the first human
  review (one issue per decade 1859-1960, two each for the 1930s-1950s). It deliberately keeps
  three annual indexes, one *American Gas Merchandising* issue and an 1859 issue with no
  printed page numbers.

Run them with `corpus-ingest run -c pub_pipeline-gas-journal --ids fixtures/pgj-sample-15.txt`.
