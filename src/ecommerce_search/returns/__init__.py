"""Return Data Foundation (Milestone 18): versioned `return-data-v1` contracts and storage.

Return data is a separate, linked dataset over catalog v1. Customer statements (reason codes,
comments, complaints, reviews) and inspection observations are stored as distinct records;
nothing here classifies a return, infers fault or responsibility, or decides a refund. Every v1
record must be declared synthetic. Contracts: `docs/m18/contracts.md`.
"""
