"""Retrieval benchmark harness for quipu.

A benchmark is a corpus, a query set with ground truth, and a metric. This package runs quipu's
REAL retrieval path (markdown store -> SQLite FTS5 -> `quipu.index.search`) over a corpus and
reports what it finds. Nothing here re-implements retrieval: the number is whatever the shipped
search returns.
"""
