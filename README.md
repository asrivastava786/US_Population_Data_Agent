# US Census Chat Agent

> A production-quality, deterministic chat agent answering natural-language questions about US demographic, economic, educational, and housing data—grounded directly in the **SafeGraph Open Census** dataset.


* **Live Demo:** [(https://us-population-data-agent.onrender.com/))]*(Hosted on Render free tier—please allow ~60s for cold start on initial request).*

---

### Example Conversation Flow

```text
User: "What is the population of Texas?"
Agent: "The population of Texas is 28,635,442. (ACS 2016-2020 5-year estimates)"

User: "What about Florida?"
Agent: "The population of Florida is 21,216,924. (ACS 2016-2020 5-year estimates)"

User: "How has it changed since 2010?"
Agent: "Trends/change over time are not covered I can help with population, income, employment, poverty, education and housing at national, state or county level."

User: " Top 5 states by income ?"
Agent: "The top 5 states by income, based on median household income, are Maryland ($87,688), New Jersey ($85,954), Massachusetts ($85,011), Hawaii ($83,922), and Connecticut ($80,224). If you are interested in mean household income, the top states are New Jersey ($117,855), Massachusetts ($115,952), Connecticut ($115,327), Maryland ($114,221), and Hawaii ($107,324). These figures are from the ACS 2016-2020 5-year estimates."
```

---

## Architecture

The system uses a **fixed LangGraph state machine** rather than an autonomous tool-calling agent. Because the domain task shape is well-defined, a fixed execution graph provides bounded latency (maximum 3 LLM calls), fully enumerable failure modes, and a strict structural security boundary—the model physically cannot execute unvalidated SQL because no path in the graph bypasses the validation node.

```text
 ┌─────────┐      ┌────────────────┐      ┌─────────────────────────┐
 │ Browser │ ───> │ FastAPI (/chat)│ ───> │   LangGraph Pipeline    │
 └─────────┘      └────────────────┘      └────────────┬────────────┘
                                                       │
         ┌─────────────────────────────────────────────┼─────────────────────────────────────────────┐
         │                                             ▼                                             │
         │  ┌──────────────┐      ┌───────────────┐      ┌───────────────┐      ┌─────────────────┐  │
         │  │ Route Intent │ ───> │ Generate SQL  │ ───> │ Validate SQL  │ ───> │ Execute & Check │  │
         │  └──────────────┘      └───────────────┘      └───────┬───────┘      └────────┬────────┘  │
         └───────────────────────────────────────────────────────┼───────────────────────┼───────────┘
                                                                 ▼                       ▼
                                                        ┌─────────────────┐     ┌─────────────────┐
                                                        │ Security Checks │     │ Snowflake (RO)  │
                                                        └─────────────────┘     └─────────────────┘
```

---

## The Curated Semantic Layer *(Core Design Decision)*

The raw SafeGraph dataset consists of **29 wide tables per release**, comprising **~242,000 Census Block Group (CBG) rows** and **~7,500 coded columns** (`B01001e22`, etc.). This layout is inherently hostile to LLM text-to-SQL generation.

To solve this, I designed a curated semantic layer (`CENSUS_AGENT.CURATED`, located in `sql/`):

* **8 Core Query Views:** Organized across 4 domains (*demographics*, *economy*, *education*, *housing*) at 2 geographic grains (*state*, *county*).
* **Precomputed Denominators & Ratios:** Percentages are computed against true survey universes (e.g., education shares use population 25+; poverty rates use the poverty universe).
* **Explicit Policy Logic:** Encodes a `geo_type` column to cleanly manage Puerto Rico and Washington D.C. rules.
* **Schema Efficiency:** The entire queryable surface fits in **~60 prompt lines**, completely eliminating raw-catalog hallucination risks.
* **Data Integrity:** All metadata mapped column-by-column against field descriptions; 9 anchor values verified to <0.01% error against published Census figures (**US Total exact: 326,569,308**).

### Bracket-Interpolated Medians

Because median figures (such as Median Household Income) cannot be mathematically aggregated from sub-geographies:
* **Naive Approach (Weighted Mean of CBG Medians):** Produced a **+10% to +13% positive bias** against published Census numbers.
* **Curated Approach (Bracket Interpolation):** Re-derived medians directly from the underlying `B19001` income distribution brackets using Census Pareto/linear interpolation formulas.
* **Result:** Reduced variance to **~0.5%–1%** against published figures (e.g., LA County re-calculated at **$71,651** vs published **$71,358**). Mean income remains exact via `B19025` aggregates.

---

## Guardrails & Graceful Degradation

| Layer | Responsibility | Mechanism |
| :--- | :--- | :--- |
| **Router** | Intent Classification | Classifies prompts against `SCOPE_MANIFEST`. Declines out-of-scope requests (e.g., time-series trends) with specific statistical reasons. |
| **SQL Validator** | AST Security Boundary | Pure-function AST parser (`app/validate_sql.py`). Enforces single `SELECT` statements, forbids system functions/joins outside the 8-view allowlist, and guarantees `LIMIT` clauses. |
| **Snowflake Role** | Defense in Depth (designed; demo runs on trial-account role) | Production design: dedicated service user with SELECT-only on CENSUS_AGENT.CURATED (sql/03_service_role.sql provided, unapplied on the trial demo — see REFLECTION). |
| **Groundedness Gate** | Fact Checking | Verifies that every numeric claim in the generated text is backed by returned database rows. One bounded regeneration attempt is granted on failure. |
| **Graceful Fallback** | Error Handling | Second-stage SQL failures fall back to plain-language explanation with explicit raw row references. Empty query results are handled as valid responses rather than errors. |

---

## Running Locally

### Prerequisites
* Python 3.10+
* Snowflake instance with the SafeGraph Open Census dataset mounted
* OpenAI API Key (or custom endpoint)

### Setup Steps

1. **Clone & Install Dependencies**
   ```bash
   git clone https://github.com/your-username/us-census-chat-agent.git
   cd us-census-chat-agent
   pip install -r requirements.txt
   ```

2. **Configure Environment**
   ```bash
   cp .env.example .env
   # Update .env with your Snowflake credentials and OpenAI API key
   ```

3. **Deploy Database Views**
   Run the setup scripts sequentially in your Snowflake console:
   ```bash
   sql/01_curated_views.sql
   sql/02_median_poverty_upgrade.sql
   ```

4. **Launch Local Server**
   ```bash
   uvicorn app.main:app --reload
   ```
   Navigate to `http://localhost:8000` in your browser.

---

## Testing Strategy

The test suite is structured into fast local unit assertions and live end-to-end integration tests:

### Unit Tests
```bash
pytest tests/test_validate_sql.py
```
* **Scope:** 16 targeted tests verifying the security boundary (`app/validate_sql.py`).
* **Checks:** SQL injection attempts, multi-statement payloads, `UNION`-smuggling, and allowlist escapes.
* **Performance:** Runs in **<1 second**, requires zero external credentials or database connections.

### Golden Integration Suite
```bash
pytest tests/test_golden.py -m integration
```
* **Scope:** 15+ full multi-turn scenarios against live Snowflake and LLM endpoints.
* **Assertions:** Evaluates numeric tolerances against published figures, decline behaviors, false-premise refutations, and context retention across turns. 
* **Design Rule:** Tests evaluate outcome correctness and data groundedness—**never raw SQL strings**.

---

## Key Decisions & Domain Policies

* **2020 ACS 5-Year Release Scope:** While the share contains both 2019 and 2020 releases, overlapping 5-year survey windows are statistically non-comparable (they share 4 of 5 sampling years and changed block-group boundaries). Trend queries are intentionally declined with an explanation.
* **Territory & DC Policy:** US national totals exclude Puerto Rico by default to align with Census headline reporting conventions (326.6M baseline). Puerto Rico and DC remain explicitly queryable via the `geo_type` filter.
* **Source Attribution:** Responses explicitly cite the instrument (*"ACS 2016–2020 5-Year Estimates"*) to distinguish these statistical estimates from 2020 Decennial Census counts.
* **Model Agnostic Abstraction:** The LLM client is abstracted behind a single `llm()` seam. Models are pinned to specific snapshot versions (`gpt-4.1` family) at `temperature=0` for strict evaluation determinism.

---

> For a complete architectural breakdown, AI tool usage reflections, and deferred production items, see `REFLECTION.md`.
