from fastapi import FastAPI

from app.api import documents, review, ui, upload

# Schema is managed by `alembic upgrade head` (the `migrate` service in
# docker-compose.yml runs before this service starts), not by the app itself -
# see app/queue/worker.py's run_forever() docstring for why two services both
# racing to create tables on startup is a bug, not a convenience.
app = FastAPI(title="Invoice Extractor")

app.include_router(upload.router)
app.include_router(documents.router)
app.include_router(review.router)
app.include_router(ui.router)
