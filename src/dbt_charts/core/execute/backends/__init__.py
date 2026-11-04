"""Backends: run a fully resolved query against a resolved source.

Each backend implements the ``Backend`` protocol in ``base``; a host picks one per
source kind through ``BackendRegistry``.
"""
