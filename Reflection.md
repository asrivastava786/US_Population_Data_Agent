# Reflection

## Development Process

My approach prioritized data truth over application logic. During the first two hours, I focused entirely on data exploration before making any architectural decisions. Taking a "metadata-first" approach, I used the field descriptions as a Rosetta stone to resolve discrepancies between the documentation (which claimed 2016-2020 releases) and the verified share contents (which contained 2019 and 2020 data, despite the listing stating 2019).

Before writing the agent, I established strict data validation discipline:
*   Ran FIPS fan-out checks and orphaned-CBG checks.
*   Verified median NULL coverage (93.5%).
*   Cross-referenced nine data anchors against published figures to ensure exact US total matches.

Only after the data was grounded did I move to the agent. I built validator-first, treating the security boundary as a pure function with dedicated tests. From there, I locked in the LangGraph pipeline, refined the prompts, handled deployment, and established the golden test suite.

## Key Architectural Decisions

Every major decision was made to constrain the LLM and guarantee deterministic accuracy:

*   **Single ACS release (2020) with trends structurally out of scope:** The share contains both 2019 and 2020 releases, making trend questions like "how has X changed since 2017?" temptingly computable—but they would be wrong twice over. The releases share four of five sample years, and they don't even share block-group boundaries (220k vs 242k CBGs). I made trend questions unanswerable by design and decline them with the statistical reason.
*   **From raw tables to a curated semantic layer:** The share exposes the data as 29 wide tables per release (e.g., `2020_CBG_B01` … `C24`), each ~242k rows (one per Census Block Group) with ~7,500 columns named in ACS code (`B01001e22` = "males 62-64"), plus metadata tables. Three properties make this raw form hostile to text-to-SQL: the semantics live in a lookup table rather than the column names; there are no state or county rows (geography must be derived from FIPS digit prefixes of the CBG key); and physical quirks like mixed-case quoted columns and digits starting table names are exactly the details LLM-generated SQL gets intermittently wrong.
    *   I converted this into 12 views in my own schema, 8 of which form the agent's queryable surface: four topics (demographics, economy, education, housing) at two grains (state, county). They feature self-explanatory column names, geography pre-aggregated via FIPS prefixes, percentages precomputed against the correct survey universes (so the LLM never has to guess the denominator), a `geo_type` column encoding the PR/DC policy, and a machine-readable `SCOPE_MANIFEST`. 
    *   The other four views are implementation details deliberately kept off the surface to avoid confusing the model (e.g., intermediate median-interpolations). 
    *   **The payoff is threefold.** *Reliability:* the model writes `SELECT total_population FROM STATE_DEMOGRAPHICS WHERE state='TX'` instead of aggregating 242k rows over coded columns—shrinking the generation task until it barely has room to hallucinate. *Verifiability:* correctness moved into deterministic SQL validated once against the dataset's own metadata and published anchors, instead of being re-derived per question. *Practicality:* the 8-view schema fits in ~60 prompt lines on every request, which the raw catalog never could.
*   **Fixed pipeline vs. tool-calling agent:** A fixed pipeline guarantees bounded latency, creates a highly testable system, and provides a much stronger structural security boundary than an open-ended tool caller.
*   **Deterministic groundedness gate vs. runtime LLM-as-judge:** Relying on an LLM-as-judge at runtime introduces latency and correlated errors. Instead, I built a deterministic groundedness gate for production and kept the LLM-as-judge strictly offline for evaluations.
*   **Bracket-interpolated medians:** The naive approximation for median calculations introduced a +10-13% bias against published figures. I replaced this with bracket-interpolated medians, re-verifying the results to achieve <1% variance.
*   **Model choice as an environment variable:** I matched model capability to task difficulty, prioritizing instruction-following over reasoning. Models are pinned to specific snapshots with temperature set to 0 for strict reproducibility, and the golden suite acts as the final arbiter for any model swaps.

## How I Used AI Tools

I used an AI pair programmer throughout the build for SQL and code generation, as well as code review. However, I maintained strict verification against source metadata at every step. 

There were several concrete catches that reinforced this discipline:
*   **DDL Verification:** Assistant-generated DDL was verified column-by-column against the dataset's own field descriptions before I trusted it.

**The operationalized lesson:** AI-generated SQL must be run by a human, statement by statement. Files in Git remain the absolute source of truth, and tests must be treated as immutable expectations—never edited simply to make a failing suite pass.

## Edge Cases Identified but Not Fully Addressed

*   **City/place-level questions:** These currently lack a place-to-county crosswalk for accurate resolution.
*   **Housing medians:** These remain weighted approximations (unlike income medians, which are interpolated; the same technique would apply here given more time).
*   **Missing median income:** Approximately 6.5% of Census Block Groups lack median income data, meaning household weighting implicitly discounts them.
*   **County-name collisions:** Ambiguity exists across states when a user queries a county name without specifying the state.
*   **Multi-intent questions requiring query decomposition:** Found by the eval suite, full breakdown of multi-part queries is currently deferred.

## Production Readiness (Known, Deferred Deliberately)

I shipped these known gaps as an assessment rather than implementations: at demo concurrency, none of them bind, and a professional customer handoff is working code plus an honest readiness list, not silent 3am infrastructure changes.
*   **Infrastructure & Scaling:** Per-request Snowflake connections would need connection pooling under real concurrency. The current in-memory session store needs Redis with TTL for multi-replica deployments.
*   **Resiliency:** LLM calls require retry/backoff mechanisms and a strict wall-clock budget across the entire pipeline.
*   **Observability:** The system needs structured per-request logs (message → route → SQL → outcome → per-node latency, keyed by session ID), core metrics (decline rate, retry rate, cost/request), and distributed tracing. 
*   **Load Testing:** While per-request latency is asserted at <30s in the golden suite, the p95 latency under concurrent load remains unmeasured. 
*   **Security:** I left the demo unauthenticated per the brief's FAQ; in production, this endpoint would sit behind proper authentication and rate limiting.

## What I'd Do Differently / With More Time

Beyond the production-readiness items outlined above, given more time I would implement:
*   **Dedicated RBAC Role:** A dedicated read-only service user (`CENSUS_READER`) to replace the personal account credentials.
*   **User Experience:** Streaming responses paired with per-stage progress indicators in the UI to mask latency.
*   **Performance Optimization:** Semantic caching of repeated or structurally equivalent questions to cut costs and speed up response times.
*   **Data & Feature Depth:** Implementing housing-median bracket interpolation (applying the exact technique used for income medians) and integrating a place-to-county crosswalk to resolve city-level inquiries accurately.
*   **Native Platform Integration:** Switching to the Cortex `COMPLETE` variant behind the `llm()` seam on a non-trial account—fulfilling the all-in-Snowflake architecture I originally preferred.

## Testing Strategy & Tradeoffs

The testing strategy is split into two distinct layers to balance speed, cost, and reliability.

*   **Unit layer:** The validator runs against an adversarial suite. It executes in under a second and is fully CI-friendly.
*   **Integration layer:** A golden end-to-end suite runs against live services. It uses tolerance-based numeric assertions on published figures and behavior assertions for safe declines and multi-turn interactions. Crucially, it deliberately never asserts on raw SQL strings.

The suite earned its keep during the build. It caught the router inappropriately declining "top 5 counties in California" as sub-county geography (which I fixed with boundary examples). It flagged an unhandled false-premise pattern ("why is Wyoming the most populous state?"—the agent now refutes the premise directly from the row data). It also caught partial-answer behavior on multi-intent questions like "richest and poorest county"; the answer now explicitly names what's missing, even though full query decomposition is deferred. Separately, the groundedness gate's first field failure was a false positive where the regex read the "2016-2020" vintage suffix as a negative number. This was caught precisely because the fallback mechanism made the failure visible rather than silent, and I subsequently pinned it with unit tests. That specific bug was a perfect example of AI-tools discipline: my own generated code, caught by my own safety design.

**Honest limits:** The golden tests cost money and depend on live services, so they are marked and run separately. Determinism relies on pinned models and a temperature of 0. To improve this further, I would add LLM-graded soft-rubric evaluations (checking for decline quality and the presence of necessary caveats) on top of the numeric golden suite, and a tiny concurrent load test to assert against the 60s requirement.
