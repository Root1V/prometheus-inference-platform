# Inference Engines — Landscape & Recommendation

Research deliverable for `memory/roadmap.md` RM-06. Prometheus currently runs a single
inference backend — `llama-server` (llama.cpp), spawned as a bare-metal OS process and
managed directly by `runtime/manager/core` (start/stop/pause/resume, PID tracking, port
allocation, capacity checks via `psutil`). This page evaluates whether that should change
as the platform grows into distributed hosts (RM-08) and new model modalities (RM-09), and
is meant to be read before designing either of those.

**Status**: recommendation only — no code changes implied by this page. RM-08/RM-09 should
treat the recommendation below as their starting brief.

---

## Target hardware

1. **Apple Silicon** (MacBook Pro M4 Max) — unified memory, Metal GPU.
2. **NVIDIA DGX Spark** — GB10 Grace-Blackwell "personal AI supercomputer": 20-core Arm
   CPU, Blackwell GPU, 128GB **coherent unified memory** (CPU+GPU share one pool, Arm+CUDA
   instead of Mac's Arm+Metal), ~1 PFLOPS FP4 sparse.
3. **Generic Linux/NVIDIA servers** — the existing RHEL/Ubuntu-DGX deployment targets,
   discrete-VRAM datacenter or consumer GPUs.

## Per-engine findings

### llama.cpp / `llama-server` (current backend)

- **Hardware**: runs everywhere — Metal, CUDA, ROCm/Vulkan, CPU-only x86/ARM. Broadest
  reach of any engine here, including DGX Spark's ARM+Blackwell combo, with zero extra
  work.
- **Throughput**: optimized for single-user, low-latency serving. No continuous
  batching/paged-attention at vLLM's level — concurrent-request throughput degrades faster
  under load. On DGX Spark: ~1,956 tok/s prefill / ~60 tok/s generation on GPT-OSS-120B
  (MXFP4) at short context, dropping to ~1,027 tok/s prefill at 32K
  ([source](https://github.com/ggml-org/llama.cpp/discussions/16578)).
- **Quantization**: GGUF native — the richest, most battle-tested single-box quantization
  ecosystem. No AWQ/GPTQ.
- **Multimodal**: growing but partial. `libmtmd` handles vision (CLIP/SigLIP) and audio
  (Whisper-style) via mmproj files through one embedding pipeline. No image/video
  generation or dedicated embedding-serving mode yet (on the public roadmap per a FOSDEM
  2026 talk).
- **Operational model**: exactly what the manager already assumes — single static binary,
  one process per model, low idle RSS, trivial `psutil`/PID tracking.
- **Maturity**: very mature for single-user text serving; behind vLLM/SGLang in modality
  breadth.

### vLLM

- **Hardware**: primary target is NVIDIA CUDA; official support for AMD ROCm, Intel XPU,
  and CPU backends (x86, ARM64, Apple Silicon **CPU-only**, IBM Z). **GPU acceleration on
  Apple Silicon is not in vLLM core** — only via `vllm-metal`, an explicitly
  community-maintained third-party plugin (not an official vLLM project) that uses MLX as
  its actual compute backend underneath. Treat vLLM-on-Mac as experimental, not
  production-ready.
- **DGX Spark**: runs natively on Spark's ARM+CUDA stack and is a commonly recommended
  choice there for concurrent workloads (see DGX Spark section).
- **Throughput**: built for high-concurrency batched serving — continuous batching,
  PagedAttention, prefix caching. Scales far better than llama.cpp with many simultaneous
  requests, at the cost of higher baseline memory/startup overhead.
- **Quantization**: strong native AWQ/GPTQ (Marlin kernels), good FP8. **GGUF support
  exists but is explicitly discouraged by the vLLM community** — high overhead. GGUF
  models in the current registry would need re-quantization to get good vLLM performance.
- **Multimodal**: broadest coverage of the four. Core vLLM already serves VLMs and
  embedding models. A newer sibling project, **vllm-omni** (officially under the
  vllm-project org, released Nov 2025), extends to audio, image, video, TTS, and
  diffusion-based generation. The most credible "one engine, many modalities" story here —
  though vllm-omni is under a year old.
- **Operational complexity**: long-running Python server, heavier startup (model
  compile/warm-up), larger and less predictable idle RSS than `llama-server`. Typically
  Docker + OpenAI-compatible HTTP endpoint. Still "one process, one port" — `psutil`-based
  capacity checks still work, but RSS/startup-time assumptions need per-engine parameters.
- **Maturity**: de facto standard for production high-throughput self-hosted serving on
  NVIDIA. `vllm-omni`/`vllm-metal` are newer and less proven.

### MLX (`mlx-lm` / `mlx-vlm`)

- **Hardware**: Apple Silicon is the native and only production target. MLX now has an
  **experimental CUDA backend** (`MLX_BUILD_CUDA=ON`), but coverage is incomplete and it's
  positioned for "author on Mac, test on Linux," not production serving on Spark/Linux.
- **Throughput**: tuned for Apple Silicon's unified-memory architecture — competitive with
  or better than llama.cpp on Mac for many models. Single-user/moderate-concurrency
  oriented, not built for vLLM/SGLang's massive-batch regime.
- **Quantization**: own native format + conversion pipeline; doesn't consume GGUF
  directly. The `mlx-community` HF org hosts ~4,800 pre-quantized MLX models (LLM, VLM,
  audio, image-gen), so conversion is usually a non-issue in practice.
- **Multimodal**: `mlx-vlm` (vision-language), `mlx-whisper` (audio), example pipelines
  for image generation. Real coverage, but assembled from several sub-projects rather than
  one packaged server.
- **Operational model**: `mlx-lm` bundles run/quantize/serve into one CLI+HTTP toolchain —
  comparable simplicity to `llama-server` on Mac. Natural fit for the manager's existing
  process-spawning model, but Apple-only.
- **Maturity**: mature and actively developed by Apple; widely regarded (with MLC-LLM) as
  the most production-ready Apple-Silicon-native runtime. Smaller ecosystem than
  vLLM/SGLang.

### SGLang

- **Hardware**: CUDA-first like vLLM, but broader than assumed: AMD ROCm, Intel XPU,
  Ascend NPU, Moore Threads GPUs, and — new in 2026 — Arm Neoverse CPUs and
  Blackwell-generation GPUs (GB300/B300). A **native Apple Silicon/MLX backend landed in
  SGLang v0.5.10** in 2026, validated so far mainly on sub-1B models — genuinely new, not
  yet broadly proven.
- **Throughput**: direct competitor to vLLM for high-concurrency batched serving.
  RadixAttention (prefix-cache tree) tends to win on heavy shared-prefix workloads
  (agentic/tool-calling, multi-turn chat).
- **Quantization**: AWQ, GPTQ (Marlin), FP8, INT8, and GGUF listed as supported/in
  progress — slightly broader stated coverage than vLLM, though GGUF is likely still
  second-class versus llama.cpp's native handling.
- **Multimodal**: explicitly covers "decoder-only LLMs, multimodal, embedding, reward, and
  diffusion models" per its own docs. Comparable ambition to vllm-omni, inside the core
  project rather than a satellite one.
- **Operational complexity**: same profile as vLLM — long-running Python server, Docker-
  typical, heavier than `llama-server`.
- **Maturity**: very mature for CUDA high-concurrency serving (used by major LLM API
  providers). Arm and Apple/MLX support are recent 2026 additions — promising but young;
  validate before betting DGX Spark or Mac production traffic on those paths.

## DGX Spark specifics

NVIDIA ships DGX Spark with CUDA 13, cuDNN, NCCL, TensorRT, and **TensorRT-LLM**
preinstalled, plus containerized **NIM microservices** via NGC, and publishes an official
[`dgx-spark-playbooks`](https://github.com/NVIDIA/dgx-spark-playbooks) repo with a
step-by-step NIM-LLM playbook. NVIDIA's own recommended turnkey path is NIM
(containerized, TensorRT-LLM-backed).

Community/practitioner consensus converges on a four-way split relevant to how the
manager would pick a backend per workload:

- **vLLM / SGLang** — best for multi-user/concurrent workloads; both run natively on
  Spark's ARM+CUDA stack.
- **llama.cpp** — simplest, single-user path; runs well but behind vLLM in cited
  benchmarks, and sensitive to Linux kernel version for cold-start load time (NVIDIA's
  6.17.1 kernel with `NO_PAGE_MAPCOUNT` roughly halves load time for large models).
- **TensorRT-LLM** — best raw single-stream tokens/sec via compiled CUDA kernels and
  INT4/FP8, but its build/tuning target is genuinely datacenter Blackwell/Hopper/Ada;
  Spark's consumer-class GB10 is a secondary target, described by practitioners as
  experimental there with real engine-compile friction.

(Primary source for the four-way split:
[Medium — "Four Inference Engines, One Box"](https://medium.com/@michael.hannecke/four-inference-engines-one-box-when-to-use-which-on-the-dgx-spark-6b32a53db768)
— a single secondary source, treat as directional; corroborated in kind by an
[NVIDIA developer forum benchmark thread](https://forums.developer.nvidia.com/t/measured-inference-benchmarks-on-a-single-dgx-spark-same-harness-across-ollama-llama-cpp-and-vllm-notes-data-published/379766).)

**Does Spark change the calculus vs. a standard datacenter NVIDIA server?** Somewhat:

- It's ARM, not x86 — llama.cpp/vLLM/SGLang all build and run on ARM, but verify container
  base images and prebuilt wheels are ARM-compatible; some vLLM/SGLang PyPI wheels are
  x86-only and need a source build on Arm.
- It's unified-memory, consumer-class Blackwell, not discrete-VRAM datacenter silicon —
  this favors llama.cpp/vLLM's memory-aware paths over TensorRT-LLM, which assumes
  datacenter memory/interconnect characteristics Spark doesn't fully share.
- NVIDIA's preferred packaging (NIM) is container-based and heavier-weight than the
  "spawn a binary, track the PID" model the manager uses today — leaning on NIM for Spark
  nodes would need a genuinely different launch/monitor path (container lifecycle, not
  process lifecycle) alongside whatever handles llama.cpp/vLLM/SGLang binaries.

## Recommendation

**Mixed strategy, not a single engine** — hardware and modality requirements differ enough
that no single engine serves all of it well.

| Node | Primary engine | Why |
|---|---|---|
| Mac (M4 Max) | **MLX** (`mlx-lm` + `mlx-vlm`/`mlx-whisper`) | Only mature production-grade path for Apple Silicon; matches `llama-server`'s "single lightweight process" profile the manager already knows. Keep llama.cpp/Metal as a fallback for models MLX doesn't cover — cheap to run both side by side on Mac. |
| DGX Spark | **vLLM** (or SGLang) for concurrent/API-serving; **llama.cpp** for single-user/simple cases | Both vLLM and SGLang run natively on Spark's ARM+Blackwell stack today; llama.cpp stays valuable for operational simplicity and GGUF compatibility with the existing registry. Treat TensorRT-LLM/NIM as an optional later add for teams willing to take on container-based lifecycle management for single-stream throughput. |
| Generic Linux/NVIDIA servers | **vLLM** primary, **SGLang** alternative for prefix-heavy/agentic workloads | Both are mature, CUDA-native, and this is the deployment target each was actually built for — no Apple/Arm caveats apply. |

### What this adds to the manager's job (concretely, for RM-08/RM-09 to scope)

1. The manager currently knows one launch shape: spawn the `llama-server` binary, track
   PID, poll a port. Adding vLLM/SGLang means a second shape: heavier Python server
   processes with longer, non-trivial startup (model compile/warm-up), larger and less
   predictable idle RSS, launched via `python -m vllm.entrypoints.openai.api_server ...`
   or a container rather than a static binary. `psutil`-based capacity checks still work,
   but the RSS/startup-time assumptions currently tuned for llama.cpp need per-engine
   parameters.
2. Adding MLX for the Mac node is comparatively low-complexity — still "one process, one
   port," just a different binary/CLI (`mlx_lm.server`) with its own model-format
   expectations (MLX-format weights, not GGUF) and its own health-check conventions.
3. `registry.yaml` needs new fields regardless of hardware: `backend` (which engine) and
   `quant_format` (gguf/awq/gptq/fp8/mlx), and likely separate download-source fields per
   format, since the same logical model will exist as multiple quantized artifacts across
   engines (a GGUF for llama.cpp on Spark, an MLX conversion for the Mac, possibly an
   AWQ/FP8 build for vLLM on Spark/Linux).
4. If NIM/containers are ever adopted for Spark, that's a third, structurally different
   launch shape (container lifecycle vs. process lifecycle) — defer until there's a
   concrete need; vLLM/SGLang as bare processes already cover Spark's concurrent-serving
   case without adding Docker/NGC as a manager dependency.

### Is there a single engine across all modalities?

No engine cleanly covers LLM + VLM + audio + image/video-gen + embeddings across all three
hardware targets at once — but within one hardware family there are credible "mostly one
engine" answers:

- **CUDA/Linux (Spark + generic servers)**: **vLLM + vllm-omni** is the strongest
  single-family candidate — core vLLM covers LLM/VLM/embeddings, vllm-omni extends to
  audio/TTS/image/video generation. SGLang makes a similar all-in-one claim
  (LLM/multimodal/embedding/reward/diffusion) and is a legitimate alternative. Both
  surfaces are new/fast-moving (vllm-omni is under a year old) — validate specific
  modality support per model before depending on it.
- **Apple Silicon**: **MLX** is the closest thing to a unified answer (`mlx-lm` +
  `mlx-vlm` + `mlx-whisper` + example image-gen pipelines), assembled from several
  sub-projects sharing a runtime rather than one packaged multi-modal server.
- **llama.cpp** is trending toward broader modality coverage (vision + audio now real,
  image-gen/TTS on the public roadmap) but isn't there yet and is unlikely to match
  vLLM-omni/SGLang's breadth for image/video generation specifically — diffusion-model
  serving is a different computational pattern than llama.cpp's autoregressive-token core.

**Bottom line for RM-08/RM-09**: multiple engines are unavoidable regardless of the
hardware question — MLX's production maturity is Apple-only, vLLM/SGLang's is CUDA-only.
The manager's real design challenge is **per-hardware backend selection**, not
per-modality backend selection: within each hardware family, one engine (MLX on Mac; vLLM
or SGLang on CUDA) can plausibly cover most or all target modalities.
