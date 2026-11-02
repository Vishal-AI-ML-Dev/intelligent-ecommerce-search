"""Return Reason Intelligence (Milestone 19), Part 1: the deterministic complaint sorter.

Predictions here are an inferred layer over M18 records. They never change those records and
are not decisions: there is no refund, fault, abuse or root-cause outcome, and a suggested
responsible area is a routing hint, not a fault assignment. Rule-confidence tiers are not
probabilities. Progress and limitations: `docs/m19/progress.md`.
"""
