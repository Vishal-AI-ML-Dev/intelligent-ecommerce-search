"""Seller listing normalization (Milestone 13).

Raw seller listings are untrusted input. This package defines the intake contract (exact raw
bytes, listing identity), the versioned rule registry and the typed normalization-output
contracts. Raw input, deterministic normalized values, unverified text candidates and
conflicts are kept separate; nothing here writes to the searchable catalog.
"""
