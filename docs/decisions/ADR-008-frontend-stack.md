# ADR-008: Frontend stack: React, TypeScript and Vite, served same-origin by FastAPI

## Status

**Accepted (owner decision, 2026-10-22).** Recorded before any frontend code exists. Nothing is
installed and no versions are pinned by this ADR. Versions, the lockfile and the exact directory
path are fixed in the M25A milestone plan and its review.

## Date

2026-10-22

## Context

`docs/MASTER_PLAN.md` §28, "Execution order (amended 2026-10-22)", starts the remaining work with
M25A, a buyer Search page over the existing `POST /search/reranked`. Later milestones add the
Listings UI (M14–M15), the shopping-assistant UI (M16–M17), the returns UI (M18–M22) and the M21
review queue UI; M25B unifies and hardens them. The locked stack (§5) names no frontend
technology. The repository has no frontend code, no CORS configuration and no static-file mount.
The stack must serve all of those UIs, so that M25A is not a throwaway first frontend.

## Decision

| Concern | Decision |
|---|---|
| UI library and language | React with TypeScript |
| Build tool | Vite |
| Browser smoke tests | Playwright |
| Codebase | One frontend codebase with domain-specific modules (search first; listings, assistant, returns and review later, each created only in its owning milestone) |
| Shared code | Only code that the current milestone actually uses across the app shell or more than one module. No speculative shared framework or component library. |
| Integration | Same-origin with the existing FastAPI application, which serves the built assets. A Vite development server may proxy API calls to FastAPI during local development only. No CORS change is needed for the served build. |
| Backend | The existing FastAPI application is the only backend. No second backend, no Next.js or other server-side rendering, and no search, ranking or business logic in the frontend. |
| API contracts | The frontend consumes the existing typed response models (for M25A, `RerankedSearchResponse`). A new or changed endpoint needs its own milestone-scoped contract. |

## Consequences

- **New toolchain.** Node.js, a package manager and the frontend dependencies become a new
  development and build dependency beside Python and uv. Installing them is part of M25A
  implementation, after its plan is approved, not part of this ADR.
- **Python-only workflows unaffected.** Backend tests and the core search engine keep working
  without the frontend toolchain.
- **Browser tests.** Playwright smoke tests exercise the UI-to-API flow. Fixture-backed (mocked
  API) runs and runs against a live local stack are reported separately. A `TestClient` check
  that a page is served is not a browser test.
- **CI.** Running Playwright in CI is automated in M23A; M25A's own acceptance already requires
  the smoke path to pass locally.

## Alternatives considered

- **Server-rendered templates in FastAPI:** no second toolchain, but a poor fit for the later
  interactive retailer UIs (evidence viewer, review queue, listing diffs).
- **Next.js or another SSR framework:** would add a second server runtime beside FastAPI, which
  the owner ruled out.
- **Separate frontend per domain:** duplicates the shell, API client and build setup.

## References

- `docs/MASTER_PLAN.md` §5, §23, §28 (execution order, M25A scope, frontend ownership)
- `docs/architecture.md` §9 (API surface)
