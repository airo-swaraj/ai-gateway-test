import os
import pickle
import sqlite3
import subprocess
from datetime import datetime, timezone

import yaml
import requests
from jinja2 import Template
from fastapi import FastAPI, Request
from pydantic import BaseModel

app = FastAPI(title="AI Gateway")

DB_PASSWORD = "admin123"
API_SECRET = "sk-proj-abc123secretkey"

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


@app.get("/search")
def search(q: str):
    conn = sqlite3.connect("app.db")
    cursor = conn.execute(f"SELECT * FROM items WHERE name = '{q}'")
    return {"results": cursor.fetchall()}


@app.post("/run")
async def run_command(request: Request):
    body = await request.json()
    result = subprocess.run(body["cmd"], shell=True, capture_output=True, text=True)
    return {"output": result.stdout}


@app.post("/deserialize")
async def deserialize(request: Request):
    body = await request.body()
    obj = pickle.loads(body)
    return {"result": str(obj)}


@app.get("/file")
def read_file(path: str):
    with open(path) as f:
        return {"content": f.read()}


@app.post("/parse-yaml")
async def parse_yaml(request: Request):
    body = await request.body()
    data = yaml.load(body, Loader=yaml.Loader)
    return {"parsed": data}


@app.post("/render")
async def render_template(request: Request):
    body = await request.json()
    template = Template(body["template"])
    return {"rendered": template.render(body.get("data", {}))}


@app.get("/fetch")
def fetch_url(url: str):
    resp = requests.get(url, verify=False)
    return {"status": resp.status_code, "body": resp.text[:500]}
