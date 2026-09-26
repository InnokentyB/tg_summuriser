# TG Summarizer shared-pattern applicability

Date: 2026-09-07  
Status: proposed addendum; owner review required; no extraction authorized

Source: [`portfolio_implementation_specs_addendum_2026-09-06.md`](/Users/innokentyb/Documents/Codex/2026-09-06/shared-ai-core-repo-patterns/outputs/portfolio_implementation_specs_addendum_2026-09-06.md). Full assessment: [`cross_product_hypothesis_applicability_2026-09-07.md`](/Users/innokentyb/Documents/Codex/2026-09-06/shared-ai-core-repo-patterns/outputs/cross_product_hypothesis_applicability_2026-09-07.md).

| Capability | Current grade | Decision |
|---|---|---|
| Telegram/TGArticles ingestion | `IMPLEMENTED + TESTED` | Adapt connector/job contracts; keep Telethon/channel fields local |
| Local prefilter | `IMPLEMENTED + TESTED` | Adapt explainable filtering only with product-specific false-negative evaluation |
| Dedup/freshness/idempotency | `IMPLEMENTED + TESTED` | Adapt universal identity/canonicalization; keep digest windows/vendor policy local |
| Ranking | `IMPLEMENTED + TESTED` | Keep weights and product policy local; share evaluation/exposure primitives |
| Human feedback | `IMPLEMENTED + TESTED` | Adapt event envelope; keep UI meaning and online policy local |
| Digest rank persistence | `IMPLEMENTED + TESTED` | Reuse trace pattern after adding prompt/model/policy and candidate snapshot |
| Batch cost controls | `IMPLEMENTED + TESTED` | Adapt behind provider-neutral job/AI interfaces |

Verification on 2026-09-07: 93/93 tests passed; 61 warnings concern a deprecated custom pytest-asyncio event-loop fixture. No license file was found.

TG Summarizer remains a separate product. Hybrid retrieval and agent runtime are not required without a measured search/workflow need.
