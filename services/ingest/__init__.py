"""Uploads (roadmap Track 1 M1): a photo or clip stored on this machine, content-addressed and write-once,
with the capture metadata read from it as hints, each naming where it came from.

`store` keeps the bytes, `probe` reads what the file is (kind, size, rate, streams), `metadata` reads
the hints, and `router` serves `POST /api/ingest` and the stored receipts and originals. Nothing here
calls a service outside this machine.
"""
