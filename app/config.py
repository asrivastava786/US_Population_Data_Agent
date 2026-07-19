"""Central config. Everything tunable lives here; secrets come from .env."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- LLM (OpenAI) ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "gpt-4o-mini")   # cheap+fast: classify/rewrite
MAIN_MODEL = os.getenv("MAIN_MODEL", "gpt-4o")            # SQL generation + answers
# Pinned via env so golden tests can assert against a fixed model.

# --- Snowflake (read-only role; explicit db/schema — never rely on defaults) ---
SNOWFLAKE = dict(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    database="CENSUS_AGENT",
    schema="CURATED",
    role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),  # demo: trial account;
    # production note (README): dedicated role with SELECT-only on CURATED.
)

QUERY_TIMEOUT_S = 30          # per-query Snowflake timeout
MAX_SQL_ATTEMPTS = 2          # generate -> validate/execute retry budget
HISTORY_WINDOW = 10           # messages of context for the rewriter
