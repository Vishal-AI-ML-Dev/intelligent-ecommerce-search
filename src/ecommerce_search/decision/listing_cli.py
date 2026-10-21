"""Listing decision command line (Milestone 14).

Run as `python -m ecommerce_search.decision.listing_cli`. Commands:

* `decide --database NAME --seller S --ref R [--version N] [--provider deterministic|jev]`:
  decides one stored M13 listing version (default: the latest) and appends the decision, linked
  to the version's stored normalization under the current rules. There is no default database.
  The default provider is the local deterministic baseline (no model, no network). `--provider
  jev` sends the listing's title, description and claims to the configured remote Jev service,
  so it also needs `--send-listing-to-jev` and an enabled Jev configuration; a Jev failure is
  recorded as a fallback, never retried here.
* `show --database NAME --seller S --ref R [--version N]`: read-only. Prints the version's
  recorded decisions exactly as stored, after checking them against their own hashes.

A route is an automated routing result: `manual_review` means a human should look, not that one
did. Errors follow the M13 command line: one concise line on stderr and exit code 1.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict

from ecommerce_search.normalization.cli import (
    CliError,
    _dump,
    _fail,
    _iso,
    _out,
    _positive_int,
    _with_database,
)

PROVIDERS = ("deterministic", "jev")


def _jev_provider():
    """The configured Jev listing provider, or a CliError while Jev is disabled."""
    from pydantic import ValidationError

    from ecommerce_search.config import get_settings
    from ecommerce_search.decision.jev_listing_provider import JevListingDecisionProvider
    from ecommerce_search.decision.jev_provider import JevConfigurationError

    try:
        config = get_settings().jev_config()
    except ValidationError:
        raise CliError("settings are incomplete or invalid") from None
    if config is None:
        raise CliError(
            "Jev is disabled or has no API key (JEV_ENABLED / TYPESAFE_API_KEY); nothing was sent"
        )
    try:
        return JevListingDecisionProvider(config)
    except JevConfigurationError as exc:
        raise CliError(f"the Jev configuration is invalid ({exc}); nothing was sent") from None


def cmd_decide(args: argparse.Namespace) -> int:
    from ecommerce_search.decision.listing_provider import (
        DeterministicListingDecisionProvider,
        ListingDecisionFailed,
    )
    from ecommerce_search.decision.listing_routing import RoutingInputMismatch
    from ecommerce_search.decision.listing_store import decide_and_store

    if args.provider == "jev" and not args.send_listing_to_jev:
        raise CliError(
            "--provider jev sends listing text to a remote service; pass --send-listing-to-jev "
            "to confirm. Nothing was sent"
        )
    _out(f"target database: {args.database}")
    provider = _jev_provider() if args.provider == "jev" else DeterministicListingDecisionProvider()
    try:
        result = _with_database(
            args.database,
            lambda engine: decide_and_store(engine, args.seller, args.ref, args.version, provider),
        )
    except (ListingDecisionFailed, RoutingInputMismatch):
        raise CliError("the provider outcome failed validation; nothing was written") from None
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            close()
    _out(f"decide: {result.summary()}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from ecommerce_search.decision.listing_routing import POLICY_VERSION, policy_digest
    from ecommerce_search.decision.listing_store import read_decisions

    stored = _with_database(
        args.database,
        lambda engine: read_decisions(engine, args.seller, args.ref, args.version),
    )
    current = {"policy_version": POLICY_VERSION, "policy_digest": policy_digest()}
    _dump(
        {
            "seller_id": stored.seller_id,
            "seller_listing_ref": stored.seller_listing_ref,
            "version_number": stored.version_number,
            "raw_sha256": stored.raw_sha256,
            "integrity": (
                "verified against the stored hashes, row counts and linked normalization run; "
                "no provider or policy was re-run"
            ),
            "note": "routes are automated routing results, not human reviews",
            "current_policy": current,
            "decisions": [
                {
                    "decision_run_id": d.decision_run_id,
                    "created_at": _iso(d.created_at),
                    "normalization": {
                        "rules_version": d.normalization_rules_version,
                        "output_sha256": d.normalization_output_sha256,
                    },
                    "question_set": {
                        "version": d.question_set_version,
                        "digest": d.question_set_digest,
                    },
                    "policy": {"version": d.policy_version, "digest": d.policy_digest},
                    # Informational only; a historical digest need not equal today's.
                    "same_as_current_policy": (
                        (d.policy_version, d.policy_digest)
                        == (current["policy_version"], current["policy_digest"])
                    ),
                    "route": d.route,
                    "deterministic_route": d.deterministic_route,
                    "outcome": d.recorded_outcome(),
                    "outcome_sha256": d.outcome_sha256,
                    "reasons": [
                        {k: v for k, v in asdict(r).items() if k != "ordinal"} for r in d.reasons
                    ],
                    "decision_sha256": d.decision_sha256,
                }
                for d in stored.decisions
            ],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ecommerce_search.decision.listing_cli", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_listing(p: argparse.ArgumentParser, why: str) -> None:
        p.add_argument(
            "--database", required=True, help=f"database name on the configured server ({why})"
        )
        p.add_argument("--seller", required=True, help="exact seller_id")
        p.add_argument("--ref", required=True, help="exact seller_listing_ref")
        p.add_argument(
            "--version", type=_positive_int, help="stored version number (default: the latest)"
        )

    p = sub.add_parser("decide", help="decide one stored listing version and append the decision")
    add_listing(p, "REQUIRED: the database that will be written; no default")
    p.add_argument("--provider", choices=PROVIDERS, default="deterministic")
    p.add_argument(
        "--send-listing-to-jev",
        action="store_true",
        help="confirm that --provider jev may send this listing's text to the remote service",
    )
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("show", help="read-only: print a version's recorded decisions")
    add_listing(p, "REQUIRED: the database to read; no default")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
