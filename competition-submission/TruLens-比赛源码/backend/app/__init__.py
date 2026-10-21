"""TruLens — L'Oreal Beauty Tech Hackathon.

"TruLens · 美妆内容可信度核验" — a beauty-content credibility verification agent
platform. This package is the FastAPI backend.

Layering (dependencies only point downwards, never upwards):

    api/       HTTP contract layer (routers, request/response wiring)
    services/  Business orchestration (file ingest, analysis pipeline, reports)
    agents/    Pure analysis logic behind the BaseAgent interface
    models/    SQLAlchemy ORM persistence
    database/  Engine, session factory, schema bootstrap
    core/      Cross-cutting: settings, enums, errors, logging, utils
"""

__version__ = "0.1.0"
