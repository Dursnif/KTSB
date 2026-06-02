from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
import asyncio
import httpx
import logging
import json
import os
import time
import uuid
from typing import Any, Dict

from gpu_gate import GPU_GATE, GpuRequest

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ollama-proxy")

app = FastAPI(title="Ollama Proxy – Kåre prioritetskø")

# ------------------------------------------------------------
# Konfig
# ------------------------------------------------------------

OLLAMA_BASE   = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11440")
FORCE_NON_STREAM = os.getenv("FORCE_NON_STREAM", "1").strip() not in ("0", "false", "False")
HTTP_TIMEOUT  = None

# Prioritetskart: source-navn → prioritet (lavere = viktigere)
PRIORITY_MAP = {
    "kaare":      0,    # Kåre er alltid sjefen
    "frigate":   10,    # Frigate venter på Kåre
    "reflection": 15,   # Nattjobb – under Frigate, romslig timeout
}
DEFAULT_PRIORITY = 20   # Ukjent kilde

# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)

def _get_request_id(req: Request) -> str:
    rid = req.headers.get("x-request-id") or req.headers.get("x-kaare-rid")
    return rid.strip() if rid else f"rid-{uuid.uuid4().hex[:16]}"

def _get_source(req: Request) -> str:
    return (req.headers.get("x-kaare-source") or "").strip().lower()

def _get_priority(source: str) -> int:
    return PRIORITY_MAP.get(source, DEFAULT_PRIORITY)

def _json_log(event: str, **fields: Any) -> None:
    payload = {"ts_ms": _now_ms(), "event": event, **fields}
    log.info(json.dumps(payload, ensure_ascii=False))

async def _read_json_body(req: Request) -> Dict[str, Any]:
    raw = await req.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

def _force_non_stream(body: Dict[str, Any]) -> Dict[str, Any]:
    if FORCE_NON_STREAM:
        body["stream"] = False
    return body

def _safe_preview(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model":       body.get("model") or body.get("name"),
        "has_images":  bool(body.get("images")),
        "image_count": len(body["images"]) if isinstance(body.get("images"), list) else 0,
        "stream":      body.get("stream"),
        "prompt_len":  len(body.get("prompt", "") or ""),
    }

# ------------------------------------------------------------
# Backend-POST med prioritetskø
# ------------------------------------------------------------

async def _proxy_post_json(*, path: str, body: Dict[str, Any], rid: str, source: str) -> Response:
    url = f"{OLLAMA_BASE}{path}"
    priority = _get_priority(source)

    # Nattmøter (reflection/dev_meeting) trenger lang tid: 27B modellasting (~110s) + lang inferens
    if source in ("reflection", "dev_meeting"):
        profile = "relaxed"
        baseline = 1200
    elif source == "kaare":
        # Kåre bruker 27B (16 GB) – modellasting fra disk kan ta > 3 min første gang
        profile = "relaxed"
        baseline = 720
    else:
        profile = "normal"
        baseline = 360
    gpu_req = GpuRequest(
        rid=rid,
        source=source or "unknown",
        container="ollama-kare",
        host_pid=os.getpid(),
        timeout_profile=profile,
        baseline_timeout_s=baseline,
        priority=priority,
    )

    GPU_GATE.acquire(gpu_req)

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            http_task = asyncio.ensure_future(client.post(url, json=body))
            cancel_task = asyncio.ensure_future(
                asyncio.to_thread(gpu_req.cancel_event.wait)
            )

            done, pending = await asyncio.wait(
                {http_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if cancel_task in done:
                _json_log("proxy_cancelled_by_gpu_timeout", rid=rid, source=source,
                          run_s=int(time.perf_counter() - t0))
                raise HTTPException(status_code=504, detail="GPU timeout – request cancelled by watchdog")

            r = http_task.result()

    except HTTPException:
        raise
    except Exception as e:
        _json_log("proxy_http_exception", rid=rid, source=source, error=str(e))
        raise HTTPException(status_code=502, detail=f"Backend connect error: {e}")
    finally:
        GPU_GATE.release(rid)

    dt_ms = int((time.perf_counter() - t0) * 1000)
    content_type = r.headers.get("content-type", "application/json")

    if r.status_code >= 400:
        _json_log("proxy_backend_error", rid=rid, source=source,
                  status=r.status_code, latency_ms=dt_ms, preview=_safe_preview(body))
        return Response(content=r.content, status_code=r.status_code, media_type=content_type)

    _json_log("proxy_ok", rid=rid, source=source, priority=priority,
              latency_ms=dt_ms, preview=_safe_preview(body))
    return Response(content=r.content, status_code=r.status_code, media_type=content_type)

# ------------------------------------------------------------
# Endepunkter
# ------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "backend": OLLAMA_BASE}

@app.get("/api/tags")
async def proxy_tags(request: Request):
    url = f"{OLLAMA_BASE}/api/tags"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(url)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))

@app.post("/api/show")
async def proxy_show(request: Request):
    rid = _get_request_id(request)
    source = _get_source(request)
    body = await _read_json_body(request)
    return await _proxy_post_json(path="/api/show", body=body, rid=rid, source=source)

@app.post("/api/generate")
async def proxy_generate(request: Request):
    rid = _get_request_id(request)
    source = _get_source(request)
    body = await _read_json_body(request)
    body = _force_non_stream(body)

    _json_log("proxy_hit", rid=rid, source=source,
              priority=_get_priority(source), preview=_safe_preview(body))

    return await _proxy_post_json(path="/api/generate", body=body, rid=rid, source=source)


@app.post("/api/chat")
async def proxy_chat(request: Request):
    rid = _get_request_id(request)
    source = _get_source(request)
    body = await _read_json_body(request)
    body = _force_non_stream(body)

    _json_log("proxy_hit", rid=rid, source=source,
              priority=_get_priority(source), preview=_safe_preview(body))

    return await _proxy_post_json(path="/api/chat", body=body, rid=rid, source=source)
