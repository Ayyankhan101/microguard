"""Dashboard backend — HTTP API and static SPA host.

Importing this package does NOT require Redis. The live endpoints import
microguard.live lazily, because microguard/live/__init__.py raises ImportError
when the 'live' extra is not installed.
"""
