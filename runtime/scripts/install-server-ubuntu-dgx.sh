#!/usr/bin/env bash
set -euo pipefail

LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp.git"
LLAMA_CPP_VERSION="${LLAMA_CPP_VERSION:-}"
BUILD_DIR="${LLAMA_CPP_BUILD_DIR:-/tmp/llama-cpp-build}"
INSTALL_PREFIX="${INSTALL_PREFIX:-$HOME/.local}"
BUILD_JOBS="${BUILD_JOBS:-$(nproc)}"

OS="$(uname -s)"
ARCH="$(uname -m)"

_use_sudo() {
    if [[ "$(id -u)" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

echo "=== llama.cpp CUDA install for NVIDIA DGX Spark ==="
echo "OS: ${OS} (${ARCH})"
echo "Build dir: ${BUILD_DIR}"
echo "Install prefix: ${INSTALL_PREFIX}"
echo ""

if [[ "${OS}" != "Linux" ]]; then
    echo "ERROR: Linux required." >&2
    exit 1
fi

echo "[1/5] Installing dependencies..."

_use_sudo apt update
_use_sudo apt install -y \
    git cmake ninja-build build-essential \
    gcc-12 g++-12 \
    python3 python3-pip python3-venv \
    pkg-config ccache

echo "[2/5] Checking CUDA..."

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi not found." >&2
    exit 1
fi

nvidia-smi

if [[ -x /usr/local/cuda-13.0/bin/nvcc ]]; then
    export PATH="/usr/local/cuda-13.0/bin:${PATH}"
    export LD_LIBRARY_PATH="/usr/local/cuda-13.0/lib64:${LD_LIBRARY_PATH:-}"
fi

if ! command -v nvcc >/dev/null 2>&1; then
    echo "ERROR: nvcc not found. Install CUDA Toolkit 13.0." >&2
    exit 1
fi

CUDA_NVCC="$(command -v nvcc)"
echo "Using nvcc: ${CUDA_NVCC}"
nvcc --version

echo "[3/5] Fetching llama.cpp..."

if [[ -d "${BUILD_DIR}/.git" ]]; then
    git -C "${BUILD_DIR}" fetch --tags --quiet
    git -C "${BUILD_DIR}" reset --hard HEAD --quiet
    git -C "${BUILD_DIR}" pull --quiet
else
    rm -rf "${BUILD_DIR}"
    git clone --depth=1 "${LLAMA_CPP_REPO}" "${BUILD_DIR}"
fi

if [[ -n "${LLAMA_CPP_VERSION}" ]]; then
    git -C "${BUILD_DIR}" fetch --tags --quiet
    git -C "${BUILD_DIR}" checkout "${LLAMA_CPP_VERSION}"
fi

echo "[4/5] Configuring and building with CUDA..."

rm -rf "${BUILD_DIR}/cmake-build"

cmake -S "${BUILD_DIR}" -B "${BUILD_DIR}/cmake-build" \
    -G Ninja \
    -DGGML_CUDA=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=/usr/bin/gcc-12 \
    -DCMAKE_CXX_COMPILER=/usr/bin/g++-12 \
    -DCMAKE_CUDA_COMPILER="${CUDA_NVCC}" \
    -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=ON \
    -DLLAMA_BUILD_SERVER=ON

cmake --build "${BUILD_DIR}/cmake-build" \
    --config Release \
    --target llama-server \
    -j"${BUILD_JOBS}"

cmake --build "${BUILD_DIR}/cmake-build" \
    --config Release \
    --target llama-bench \
    -j"${BUILD_JOBS}"

echo "[5/5] Installing binaries..."

mkdir -p "${INSTALL_PREFIX}/bin"

SERVER_BIN="$(find "${BUILD_DIR}/cmake-build" -type f -name llama-server -perm -111 | head -1)"
BENCH_BIN="$(find "${BUILD_DIR}/cmake-build" -type f -name llama-bench -perm -111 | head -1)"

if [[ -z "${SERVER_BIN}" ]]; then
    echo "ERROR: llama-server binary not found." >&2
    exit 1
fi

if [[ -z "${BENCH_BIN}" ]]; then
    echo "ERROR: llama-bench binary not found." >&2
    exit 1
fi

install -m 755 "${SERVER_BIN}" "${INSTALL_PREFIX}/bin/llama-server"
install -m 755 "${BENCH_BIN}" "${INSTALL_PREFIX}/bin/llama-bench"

hash -r || true

echo ""
echo "Installed:"
echo "  ${INSTALL_PREFIX}/bin/llama-server"
echo "  ${INSTALL_PREFIX}/bin/llama-bench"
echo ""

echo "Versions:"
"${INSTALL_PREFIX}/bin/llama-server" --version || true
"${INSTALL_PREFIX}/bin/llama-bench" --version || true

echo ""
echo "Checking missing shared libraries..."
ldd "${INSTALL_PREFIX}/bin/llama-server" | grep "not found" || echo "llama-server OK"
ldd "${INSTALL_PREFIX}/bin/llama-bench" | grep "not found" || echo "llama-bench OK"

echo ""
if [[ ":${PATH}:" != *":${INSTALL_PREFIX}/bin:"* ]]; then
    echo "Add this to ~/.zshrc:"
    echo "  export PATH=\"${INSTALL_PREFIX}/bin:\$PATH\""
fi

echo ""
echo "Done."