from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="AI Gateway")

items: dict[int, dict] = {}
_counter = 0


class Item(BaseModel):
    name: str
    description: str = ""


@app.get("/")
def root():
    return {"service": "ai-gateway", "status": "running"}


@app.get("/health")
def health():
    return {"healthy": True}


@app.get("/items")
def list_items():
    return list(items.values())


@app.get("/items/{item_id}")
def get_item(item_id: int):
    if item_id not in items:
        return {"error": "not found"}, 404
    return items[item_id]


@app.post("/items", status_code=201)
def create_item(item: Item):
    global _counter
    _counter += 1
    entry = {"id": _counter, **item.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}
    items[_counter] = entry
    return entry


@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    if item_id not in items:
        return {"error": "not found"}, 404
    del items[item_id]
    return {"deleted": item_id}
