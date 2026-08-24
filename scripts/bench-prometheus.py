#!/usr/bin/env python3
"""
bench-prometheus.py

Benchmark de throughput/latencia para Prometheus:
1) Directo contra llama-server
2) Via Prometheus Gateway

Metricas:
- total_latency_s
- time_to_first_token_s
- generated_tokens
- tokens_per_second
- gateway_overhead_s
- gateway_overhead_pct

Uso:
  python3 bench-prometheus.py \
    --direct-url http://127.0.0.1:8103/v1/chat/completions \
    --gateway-url https://127.0.0.1:8000/v1/chat/completions \
    --model gemma4-12b-qat-q4xl \
    --token "$TOKEN2" \
    --runs 5 \
    --max-tokens 256 \
    --stream
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class BenchResult:
    layer: str
    run: int
    status_code: int
    ok: bool
    total_latency_s: float
    ttft_s: Optional[float]
    generated_tokens: int
    tokens_per_second: float
    error: str = ""


def now() -> float:
    return time.perf_counter()


def count_tokens_rough(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def extract_content_and_tokens(body: Dict[str, Any]) -> tuple[str, int]:
    content = ""
    try:
        content = body["choices"][0]["message"]["content"] or ""
    except Exception:
        content = ""

    usage = body.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")

    if isinstance(completion_tokens, int):
        return content, completion_tokens

    return content, count_tokens_rough(content)


def run_non_stream(
    layer: str,
    run_id: int,
    url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    token: Optional[str],
    verify_tls: bool,
    timeout_s: int,
) -> BenchResult:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }

    t0 = now()
    try:
        r = requests.post(url, headers=headers, json=payload, verify=verify_tls, timeout=timeout_s)
        total = now() - t0

        if not r.ok:
            return BenchResult(layer, run_id, r.status_code, False, total, None, 0, 0.0, r.text[:500])

        body = r.json()
        content, completion_tokens = extract_content_and_tokens(body)
        tps = completion_tokens / total if total > 0 else 0.0

        return BenchResult(
            layer=layer,
            run=run_id,
            status_code=r.status_code,
            ok=True,
            total_latency_s=total,
            ttft_s=None,
            generated_tokens=completion_tokens,
            tokens_per_second=tps,
            error="" if content else "empty content",
        )
    except Exception as e:
        total = now() - t0
        return BenchResult(layer, run_id, 0, False, total, None, 0, 0.0, f"{type(e).__name__}: {e}")


def run_stream(
    layer: str,
    run_id: int,
    url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    token: Optional[str],
    verify_tls: bool,
    timeout_s: int,
) -> BenchResult:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
    }

    t0 = now()
    ttft = None
    text_parts: List[str] = []

    try:
        with requests.post(
            url,
            headers=headers,
            json=payload,
            verify=verify_tls,
            timeout=timeout_s,
            stream=True,
        ) as r:
            if not r.ok:
                total = now() - t0
                return BenchResult(layer, run_id, r.status_code, False, total, None, 0, 0.0, r.text[:500])

            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue

                line = raw.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()

                if line == "[DONE]":
                    break

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                delta = ""
                try:
                    delta = chunk["choices"][0].get("delta", {}).get("content") or ""
                except Exception:
                    delta = ""

                if delta:
                    if ttft is None:
                        ttft = now() - t0
                    text_parts.append(delta)

        total = now() - t0
        content = "".join(text_parts)
        generated_tokens = count_tokens_rough(content)
        decode_time = max(total - (ttft or 0.0), 0.000001)
        tps = generated_tokens / decode_time if generated_tokens else 0.0

        return BenchResult(
            layer=layer,
            run=run_id,
            status_code=200,
            ok=True,
            total_latency_s=total,
            ttft_s=ttft,
            generated_tokens=generated_tokens,
            tokens_per_second=tps,
            error="" if content else "empty stream content",
        )

    except Exception as e:
        total = now() - t0
        return BenchResult(layer, run_id, 0, False, total, ttft, 0, 0.0, f"{type(e).__name__}: {e}")


def summarize(results: List[BenchResult]) -> Dict[str, Any]:
    ok = [r for r in results if r.ok]
    if not ok:
        return {"ok_runs": 0}

    lat = [r.total_latency_s for r in ok]
    tps = [r.tokens_per_second for r in ok]
    ttft_vals = [r.ttft_s for r in ok if r.ttft_s is not None]
    toks = [r.generated_tokens for r in ok]

    return {
        "ok_runs": len(ok),
        "avg_latency_s": statistics.mean(lat),
        "p50_latency_s": statistics.median(lat),
        "min_latency_s": min(lat),
        "max_latency_s": max(lat),
        "avg_ttft_s": statistics.mean(ttft_vals) if ttft_vals else None,
        "avg_tokens": statistics.mean(toks),
        "avg_tokens_per_second": statistics.mean(tps),
        "max_tokens_per_second": max(tps),
        "min_tokens_per_second": min(tps),
    }


def print_table(results: List[BenchResult]) -> None:
    print("")
    print("Resultados por ejecucion")
    print("-" * 112)
    print(f"{'layer':<10} {'run':>3} {'ok':>5} {'http':>5} {'latency_s':>10} {'ttft_s':>10} {'tokens':>8} {'tok/s':>10} error")
    print("-" * 112)
    for r in results:
        ttft = f"{r.ttft_s:.3f}" if r.ttft_s is not None else "-"
        print(
            f"{r.layer:<10} {r.run:>3} {str(r.ok):>5} {r.status_code:>5} "
            f"{r.total_latency_s:>10.3f} {ttft:>10} {r.generated_tokens:>8} "
            f"{r.tokens_per_second:>10.2f} {r.error[:80]}"
        )


def print_summary(label: str, summary: Dict[str, Any]) -> None:
    print("")
    print(f"Resumen {label}")
    print("-" * 60)
    if summary.get("ok_runs", 0) == 0:
        print("Sin ejecuciones exitosas.")
        return

    for k, v in summary.items():
        if isinstance(v, float):
            print(f"{k}: {v:.3f}")
        else:
            print(f"{k}: {v}")


def explain(direct: Dict[str, Any], gateway: Dict[str, Any], stream: bool) -> None:
    print("")
    print("Interpretacion")
    print("-" * 60)

    if direct.get("ok_runs", 0) and gateway.get("ok_runs", 0):
        dlat = direct["avg_latency_s"]
        glat = gateway["avg_latency_s"]
        overhead = glat - dlat
        overhead_pct = (overhead / dlat * 100.0) if dlat > 0 else 0.0

        dtps = direct["avg_tokens_per_second"]
        gtps = gateway["avg_tokens_per_second"]
        tps_delta = gtps - dtps
        tps_delta_pct = (tps_delta / dtps * 100.0) if dtps > 0 else 0.0

        print(f"Overhead medio del gateway: {overhead:.3f}s ({overhead_pct:.1f}%).")
        print(f"Diferencia media de throughput gateway vs directo: {tps_delta:.2f} tok/s ({tps_delta_pct:.1f}%).")

        if overhead_pct > 20:
            print("El gateway anade overhead relevante. Revisa JWT, TLS, logging, red interna y observabilidad.")
        elif overhead_pct > 5:
            print("El gateway anade overhead moderado. Es normal con TLS, JWT, logging y proxy HTTP.")
        else:
            print("El overhead del gateway es bajo. El cuello de botella principal esta en llama-server/modelo.")

    print("")
    print("Factores que explican tokens/s altos o bajos:")
    print("- Modelo y cuantizacion: Q4 suele ser mas rapido que F16/FP32.")
    print("- Context length: ctx-size muy alto aumenta memoria y puede afectar latencia.")
    print("- n-gpu-layers: si esta en 0, estas CPU-only; si hay soporte, subir capas mejora throughput.")
    print("- Threads: demasiados o pocos threads pueden bajar throughput.")
    print("- Prompt largo: aumenta prefill antes del primer token.")
    print("- Streaming: mide mejor TTFT; no-stream mide latencia total.")
    print("- Gateway: anade TLS, JWT, autorizacion, proxy HTTP y logging.")

    if not stream:
        print("")
        print("Nota: ejecuta con --stream para medir time-to-first-token real.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--direct-url", required=True)
    p.add_argument("--gateway-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--token", default=None)
    p.add_argument("--prompt", default="Hola, responde en espanol en un parrafo que eres y para que sirves.")
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--stream", action="store_true")
    p.add_argument("--verify-gateway-tls", action="store_true")
    p.add_argument("--output-json", default="")
    args = p.parse_args()

    runner = run_stream if args.stream else run_non_stream

    results: List[BenchResult] = []

    print("Benchmark Prometheus")
    print(f"Modelo: {args.model}")
    print(f"Runs: {args.runs}")
    print(f"Modo: {'stream' if args.stream else 'non-stream'}")
    print(f"Direct URL: {args.direct_url}")
    print(f"Gateway URL: {args.gateway_url}")

    for i in range(1, args.runs + 1):
        results.append(
            runner("direct", i, args.direct_url, args.model, args.prompt, args.max_tokens, None, True, args.timeout)
        )

    for i in range(1, args.runs + 1):
        results.append(
            runner("gateway", i, args.gateway_url, args.model, args.prompt, args.max_tokens, args.token, args.verify_gateway_tls, args.timeout)
        )

    print_table(results)

    direct_summary = summarize([r for r in results if r.layer == "direct"])
    gateway_summary = summarize([r for r in results if r.layer == "gateway"])

    print_summary("direct llama-server", direct_summary)
    print_summary("gateway", gateway_summary)
    explain(direct_summary, gateway_summary, args.stream)

    if args.output_json:
        payload = {
            "results": [r.__dict__ for r in results],
            "summary": {"direct": direct_summary, "gateway": gateway_summary},
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nJSON guardado en: {args.output_json}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
