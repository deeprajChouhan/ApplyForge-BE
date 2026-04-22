from pydantic import BaseModel


class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class KnowledgeSearchResult(BaseModel):
    chunk_id: int
    content: str
    score: float
