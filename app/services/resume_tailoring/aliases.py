"""
Versioned alias groups for ATS term matching.

Each group lists spellings that mean the same thing. An ATS usually matches
the *literal* phrase from the job description, so the tailoring pipeline uses
these groups in two directions:

  • Evidence: "PostgreSQL" in the resume counts as evidence for "Postgres"
    in the JD (the candidate has the skill under another name).
  • Placement: the JD's own spelling is what gets written into the resume,
    so a literal-match ATS finds it.

Kept as data (not code) so it can move to DB / config later without touching
the matcher. Bump ALIASES_VERSION whenever the groups change — it is stored
on every tailoring report so results stay reproducible.
"""

ALIASES_VERSION = "2026-11-23.1"

ALIAS_GROUPS: list[list[str]] = [
    # Languages / runtimes
    ["javascript", "js", "ecmascript"],
    ["typescript", "ts"],
    ["python", "python3", "py"],
    ["golang", "go lang"],
    ["c#", "csharp", "c sharp"],
    ["c++", "cpp"],
    ["node.js", "nodejs", "node js", "node"],
    [".net", "dotnet", "dot net"],
    # Frontend
    ["react", "react.js", "reactjs", "react js"],
    ["next.js", "nextjs", "next js"],
    ["vue", "vue.js", "vuejs"],
    ["angular", "angularjs", "angular.js"],
    ["front-end", "frontend", "front end", "front-end development", "frontend development"],
    ["back-end", "backend", "back end", "back-end development", "backend development"],
    ["full-stack", "fullstack", "full stack"],
    ["tailwind", "tailwind css", "tailwindcss"],
    # Data stores
    ["postgres", "postgresql", "postgre sql", "postgres databases", "postgresql databases"],
    ["mysql", "my sql"],
    ["mongodb", "mongo", "mongo db"],
    ["elasticsearch", "elastic search", "opensearch"],
    ["sql server", "mssql", "ms sql"],
    ["dynamodb", "dynamo db"],
    ["vector database", "vector db", "vector store", "qdrant", "pinecone", "weaviate", "pgvector", "milvus"],
    # Cloud / infra
    ["aws", "amazon web services"],
    ["gcp", "google cloud", "google cloud platform"],
    ["azure", "microsoft azure"],
    ["kubernetes", "k8s"],
    ["docker", "containers", "containerization", "containerisation"],
    ["ci/cd", "cicd", "ci cd", "continuous integration", "continuous delivery", "continuous deployment"],
    ["terraform", "infrastructure as code", "iac"],
    ["microservices", "micro-services", "microservice architecture"],
    # APIs / backend
    ["rest", "restful", "rest api", "rest apis", "restful api", "restful apis"],
    ["graphql", "graph ql"],
    ["fastapi", "fast api"],
    ["async", "asynchronous", "asyncio", "async processing", "asynchronous processing"],
    ["message queue", "celery", "rabbitmq", "kafka", "service bus", "sqs"],
    # AI / ML
    ["machine learning", "ml"],
    ["artificial intelligence", "ai"],
    ["deep learning", "dl"],
    ["natural language processing", "nlp"],
    ["large language model", "large language models", "llm", "llms"],
    ["generative ai", "genai", "gen ai"],
    ["retrieval-augmented generation", "retrieval augmented generation", "rag"],
    ["ai agents", "llm agents", "agents", "agentic", "agentic ai", "ai agent", "autonomous agents"],
    ["prompt engineering", "prompt design", "prompting"],
    ["context engineering", "context management", "context window management"],
    ["evaluation pipeline", "evaluation pipelines", "eval pipeline", "evals", "ai evaluation",
     "llm evaluation", "llm-as-judge", "llm as a judge", "model evaluation"],
    ["embeddings", "embedding", "vector embeddings", "text embeddings"],
    ["semantic search", "vector search", "similarity search"],
    ["openai", "open ai", "gpt", "gpt-4", "gpt-4o", "chatgpt"],
    ["langchain", "lang chain"],
    ["pytorch", "torch"],
    ["tensorflow", "tf"],
    ["scikit-learn", "sklearn", "scikit learn"],
    ["computer vision", "cv"],
    # Practices
    ["agile", "scrum", "kanban"],
    ["test-driven development", "tdd"],
    ["unit testing", "unit tests", "pytest", "jest"],
    ["object-oriented programming", "oop", "object oriented"],
    ["production", "production-grade", "production-ready", "in production", "shipped to production"],
]


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("‐", "-").replace("‑", "-").split())


# term -> set of equivalent spellings (including itself)
ALIAS_INDEX: dict[str, frozenset[str]] = {}
for _group in ALIAS_GROUPS:
    _members = frozenset(_norm(m) for m in _group)
    for _m in _members:
        # A spelling may sit in two groups ("agents" vs "ai agents") — union them.
        ALIAS_INDEX[_m] = ALIAS_INDEX.get(_m, frozenset()) | _members

# Short aliases that are too ambiguous to count as evidence on their own
# ("go", "ts", "ai", "cv", "ml" ...). They still count when they are the JD's
# own spelling, but are never used to *find* a different term in the resume.
AMBIGUOUS_SHORT = frozenset({"ai", "ml", "dl", "cv", "ts", "tf", "py", "js", "node", "rest", "agents",
                             "production", "go lang", "containers", "evals", "prompting", "kanban"})
