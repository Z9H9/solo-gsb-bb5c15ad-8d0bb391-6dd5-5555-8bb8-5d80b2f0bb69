"""管网漏点隔离规划台 — FastAPI 入口。

API:
  GET  /api/network                 读取当前管网输入
  PUT  /api/network                 整体保存管网输入(校验后覆盖)
  POST /api/plan                    常规求解并保存方案
  POST /api/plan-failover           单阀失效备援求解并保存方案
  GET  /api/plans                   最近方案列表
  GET  /api/plans/{id}              方案详情(含节点/管段状态)
  POST /api/demo                    恢复内置示例管网
  POST /api/clear                   清空管网输入
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import database
from demo import DEMO_NETWORK
from solver import MAX_VALVES, NetworkValidationError, solve, solve_failover

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class NodeIn(BaseModel):
    id: str = Field(min_length=1)
    role: Literal["inlet", "leak", "must_keep", "normal"] = "normal"
    label: str | None = None

    @field_validator("id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("节点编号不能为空")
        return v


class SegmentIn(BaseModel):
    id: str = Field(min_length=1)
    endpoint_a: str = Field(min_length=1)
    endpoint_b: str = Field(min_length=1)

    @field_validator("id", "endpoint_a", "endpoint_b")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ValveIn(BaseModel):
    id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    cost: int = Field(gt=0)

    @field_validator("id", "segment_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class NetworkIn(BaseModel):
    nodes: list[NodeIn] = Field(default_factory=list)
    segments: list[SegmentIn] = Field(default_factory=list)
    valves: list[ValveIn] = Field(default_factory=list, max_length=MAX_VALVES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="管网漏点隔离规划台", version="1.0.0", lifespan=lifespan)


def _payload(net: NetworkIn) -> dict:
    return net.model_dump()


@app.get("/api/network")
def get_network() -> dict:
    with database.get_conn() as conn:
        return database.load_network(conn)


@app.put("/api/network")
def save_network(net: NetworkIn) -> dict:
    payload = _payload(net)
    try:
        # 提前用求解器的完整校验(端点存在、阀门绑定等), 保证保存的是有效管网
        from solver import validate_network
        validate_network(payload)
    except NetworkValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    with database.get_conn() as conn:
        database.replace_network(conn, payload)
    return {"ok": True, "counts": {
        "nodes": len(payload["nodes"]),
        "segments": len(payload["segments"]),
        "valves": len(payload["valves"]),
    }}


@app.post("/api/plan")
def create_plan(net: NetworkIn | None = None) -> dict:
    """常规求解。请求体可空: 为空时使用数据库中已保存的管网。"""
    with database.get_conn() as conn:
        payload = _payload(net) if net is not None else database.load_network(conn)
        try:
            result = solve(payload)
        except NetworkValidationError as exc:
            raise HTTPException(status_code=422,
                                detail={"errors": exc.errors})
        plan_id = database.save_plan(conn, result, payload)
    result["id"] = plan_id
    return result


@app.post("/api/plan-failover")
def create_failover_plan(net: NetworkIn | None = None) -> dict:
    """单阀失效备援求解(独立 API)。请求体可空: 为空时用已保存管网。

    要求计划关阀方案在正常执行及任意一枚计划阀门关不上(保持开启)时,
    全部漏点与进水点断开、全部必保点仍连通至少一个进水点。
    """
    with database.get_conn() as conn:
        payload = _payload(net) if net is not None else database.load_network(conn)
        try:
            result = solve_failover(payload)
        except NetworkValidationError as exc:
            raise HTTPException(status_code=422,
                                detail={"errors": exc.errors})
        plan_id = database.save_plan(conn, result, payload)
    result["id"] = plan_id
    return result


@app.get("/api/plans")
def get_plans(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    with database.get_conn() as conn:
        return {"plans": database.list_plans(conn, limit)}


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int) -> dict:
    with database.get_conn() as conn:
        plan = database.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan


@app.post("/api/demo")
def load_demo() -> dict:
    with database.get_conn() as conn:
        database.replace_network(conn, DEMO_NETWORK)
    return {"ok": True, "network": DEMO_NETWORK}


@app.post("/api/clear")
def clear_network() -> dict:
    with database.get_conn() as conn:
        database.replace_network(conn,
                                 {"nodes": [], "segments": [], "valves": []})
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---- 静态页面 ----
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
