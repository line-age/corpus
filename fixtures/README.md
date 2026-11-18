# Fixtures

Identifier lists for real runs against Internet Archive. Identifiers only: nothing here is
copied from the scans or OCR (L-0009 keeps fixtures public and synthetic).

- `pgj-sample-15.txt`: a 15-issue Pipeline & Gas Journal sample for human review, inside the
  1920-1961 window: three or four issues per decade, both ends of the window, and both license
  lanes (1920-1928 confirmed, 1929 onwards to verify). It keeps two annual indexes, one
  *American Gas Merchandising* issue and an issue with few printed page numbers.

Run them with `corpus-ingest run -c pub_pipeline-gas-journal --ids fixtures/pgj-sample-15.txt`.
