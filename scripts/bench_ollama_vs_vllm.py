#!/usr/bin/env python3
"""Benchmark: vLLM (NVFP4, current prod) vs Ollama (GGUF, original) for huihui_ai/Qwen3.6-abliterated:27b.

Two test modes per backend:
  raw   — direct against the model server (measures true TTFT + decode tok/s)
  pipe  — through Kaare's real /api/generate (measures end-to-end latency incl.
          system prompt, memory injection, tool loop)

Usage:
  python3 bench_ollama_vs_vllm.py raw --backend vllm --base-url http://127.0.0.1:11440 --scenario short --rounds 5
  python3 bench_ollama_vs_vllm.py raw --backend ollama --base-url http://127.0.0.1:11448 --scenario long_prefix --rounds 5
  python3 bench_ollama_vs_vllm.py pipe --base-url http://127.0.0.1:8000 --scenario short --rounds 5
"""
import argparse
import json
import sys
import time

import httpx

MODEL = "huihui_ai/Qwen3.6-abliterated:27b"
TEST_USER_ID = "benchmark_test"

# ~12k chars mimics the real static system-prompt prefix seen in logs/llm_calls.log,
# so prefix-cache behavior is representative of production load.
_FILLER = "Bakgrunnskontekst for prefix-cache-test. Dette er fyllstoff som gjentas for aa simulere en lang, stabil systemprompt-prefiks. " * 90

SCENARIOS = {
    "short": {
        "system": "Du er en hjelpsom assistent. Svar kort.",
        "user": "Hva er hovedstaden i Norge?",
        "pipe_prompt": "Fortell meg en kort vits om roboter.",
    },
    "long_prefix": {
        "system": _FILLER,
        "user": "Oppsummer med en setning hva du nettopp leste.",
        "pipe_prompt": "Sok i minnet ditt etter noe om Windows-testmaskinen og oppsummer kort.",
    },
}


def bench_raw_vllm(base_url, scenario, rounds, max_tokens=256, temperature=1.0, top_p=0.75):
    sc = SCENARIOS[scenario]
    out = []
    for i in range(rounds):
        t0 = time.perf_counter()
        first_any = None
        first_content = None
        n_chunks = 0
        usage = None
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": sc["system"]},
                {"role": "user", "content": sc["user"]},
            ],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream_options": {"include_usage": True},
        }
        with httpx.stream("POST", f"{base_url}/v1/chat/completions", json=payload, timeout=180) as r:
            if r.status_code >= 400:
                print(f"  [vllm/raw/{scenario}] round {i+1}: HTTP {r.status_code}: {r.read()[:300]}")
                out.append({"round": i + 1, "error": r.status_code})
                continue
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("reasoning") or delta.get("content"):
                        if first_any is None:
                            first_any = time.perf_counter()
                        n_chunks += 1
                    if delta.get("content") and first_content is None:
                        first_content = time.perf_counter()
        t1 = time.perf_counter()
        ttft = (first_any - t0) if first_any else None
        ttft_content = (first_content - t0) if first_content else None
        total = t1 - t0
        completion_tokens = (usage or {}).get("completion_tokens", n_chunks)
        decode_s = (t1 - first_any) if first_any else None
        tok_s = (completion_tokens / decode_s) if decode_s and completion_tokens else None
        out.append({
            "round": i + 1, "ttft_s": ttft, "ttft_content_s": ttft_content, "total_s": total,
            "completion_tokens": completion_tokens, "tok_per_s": tok_s,
        })
        print(f"  [vllm/raw/{scenario}] round {i+1}: ttft={ttft} ttft_content={ttft_content} total={total:.3f}s tokens={completion_tokens} tok/s={tok_s}")
    return out


def bench_raw_ollama(base_url, scenario, rounds, num_predict=256, num_ctx=32768, temperature=1.0):
    sc = SCENARIOS[scenario]
    out = []
    for i in range(rounds):
        t0 = time.perf_counter()
        first_any = None
        first_content = None
        eval_count = None
        eval_duration = None
        prompt_eval_count = None
        prompt_eval_duration = None
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": sc["system"]},
                {"role": "user", "content": sc["user"]},
            ],
            "stream": True,
            "options": {"num_predict": num_predict, "num_ctx": num_ctx, "temperature": temperature},
        }
        with httpx.stream("POST", f"{base_url}/api/chat", json=payload, timeout=180) as r:
            if r.status_code >= 400:
                print(f"  [ollama/raw/{scenario}] round {i+1}: HTTP {r.status_code}: {r.read()[:300]}")
                out.append({"round": i + 1, "error": r.status_code})
                continue
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                msg = obj.get("message") or {}
                thinking = msg.get("thinking")
                content = msg.get("content")
                if (thinking or content) and first_any is None:
                    first_any = time.perf_counter()
                if content and first_content is None:
                    first_content = time.perf_counter()
                if obj.get("done"):
                    eval_count = obj.get("eval_count")
                    eval_duration = obj.get("eval_duration")
                    prompt_eval_count = obj.get("prompt_eval_count")
                    prompt_eval_duration = obj.get("prompt_eval_duration")
        t1 = time.perf_counter()
        ttft = (first_any - t0) if first_any else None
        ttft_content = (first_content - t0) if first_content else None
        total = t1 - t0
        tok_s = (eval_count / (eval_duration / 1e9)) if eval_count and eval_duration else None
        prompt_tok_s = (prompt_eval_count / (prompt_eval_duration / 1e9)) if prompt_eval_count and prompt_eval_duration else None
        out.append({
            "round": i + 1, "ttft_s": ttft, "ttft_content_s": ttft_content, "total_s": total,
            "completion_tokens": eval_count, "tok_per_s": tok_s,
            "prompt_tokens": prompt_eval_count, "prompt_tok_per_s": prompt_tok_s,
        })
        print(f"  [ollama/raw/{scenario}] round {i+1}: ttft={ttft} ttft_content={ttft_content} total={total:.3f}s tokens={eval_count} tok/s={tok_s} prompt_tok/s={prompt_tok_s}")
    return out


def bench_pipe(base_url, scenario, rounds):
    sc = SCENARIOS[scenario]
    out = []
    for i in range(rounds):
        t0 = time.perf_counter()
        r = httpx.post(
            f"{base_url}/api/generate",
            json={"prompt": sc["pipe_prompt"], "user_id": TEST_USER_ID, "source": "benchmark"},
            timeout=180,
        )
        t1 = time.perf_counter()
        total = t1 - t0
        out.append({"round": i + 1, "total_s": total, "status": r.status_code})
        print(f"  [pipe/{scenario}] round {i+1}: total={total:.3f}s status={r.status_code}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["raw", "pipe"])
    ap.add_argument("--backend", choices=["vllm", "ollama"])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="short")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--out", default=None, help="write JSON results to this file")
    args = ap.parse_args()

    if args.mode == "raw":
        if args.backend == "vllm":
            results = bench_raw_vllm(args.base_url, args.scenario, args.rounds)
        elif args.backend == "ollama":
            results = bench_raw_ollama(args.base_url, args.scenario, args.rounds)
        else:
            print("raw mode requires --backend vllm|ollama", file=sys.stderr)
            sys.exit(1)
    else:
        results = bench_pipe(args.base_url, args.scenario, args.rounds)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
