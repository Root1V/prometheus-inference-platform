#!/usr/bin/env bash
# runtime/scripts/install-server.sh
# Implements: memory/specs/003-llama-cpp-runtime.md — AC-16, AC-17
#
# Builds llama-server from source and installs it on the current host.
# Supports: macOS (Apple Silicon / Metal) and RHEL 9.7 (OpenBLAS / CPU-only).
#
# macOS: Does NOT require Homebrew or admin rights.
#   cmake is resolved via: system cmake → uv tool run cmake → pip cmake
#   Binary installs to ~/.local/bin by default (no sudo needed).
#
# Usage:
#   bash runtime/scripts/install-server.sh
#
# Optional env vars:
#   LLAMA_CPP_VERSION     Git tag to check out (default: latest main)
#   LLAMA_CPP_BUILD_DIR   Scratch build directory (default: /tmp/llama-cpp-build)
#   INSTALL_PREFIX        Install prefix (default: ~/.local)

set -euo pipefail

LLAMA_CPP_REPO="https://github.com/ggerganov/llama.cpp.git"
LLAMA_CPP_VERSION="${LLAMA_CPP_VERSION:-}"
BUILD_DIR="${LLAMA_CPP_BUILD_DIR:-/tmp/llama-cpp-build}"
INSTALL_PREFIX="${INSTALL_PREFIX:-$HOME/.local}"

OS="$(uname -s)"
ARCH="$(uname -m)"

echo "=== Prometheus — llama-server install ==="
echo "  OS           : ${OS} (${ARCH})"
echo "  build dir    : ${BUILD_DIR}"
echo "  install to   : ${INSTALL_PREFIX}/bin/llama-server"
if [[ -n "${LLAMA_CPP_VERSION}" ]]; then
    echo "  version      : ${LLAMA_CPP_VERSION}"
else
    echo "  version      : latest"
fi
echo ""

# ── cmake resolver — no Homebrew required ─────────────────────────────────────
# Resolves cmake in priority order:
#   1. System cmake (if already on PATH)
#   2. uv tool run cmake  (available when the project's uv environment is active)
#   3. python3 -m cmake   (fallback via pip cmake wheel)
_cmake() {
    if command -v cmake &>/dev/null; then
        cmake "$@"
    elif command -v uv &>/dev/null; then
        uv tool run cmake "$@"
    elif python3 -c "import cmake" &>/dev/null 2>&1; then
        python3 -m cmake "$@"
    else
        echo "ERROR: cmake not found." >&2
        echo "       Install via: uv tool install cmake  OR  pip install cmake" >&2
        exit 1
    fi
}

# ── Step 1: Prerequisites ──────────────────────────────────────────────────────

echo "[1/4] Checking prerequisites..."

if [[ "${OS}" == "Darwin" ]]; then
    # macOS Apple Silicon — Metal backend.
    # No Homebrew required — cmake is resolved via _cmake() above.
    # Xcode Command Line Tools (clang, git, make) must be present.
    if ! command -v git &>/dev/null; then
        echo "ERROR: git not found." >&2
        echo "       Install Xcode Command Line Tools: xcode-select --install" >&2
        exit 1
    fi
    if ! xcode-select -p &>/dev/null; then
        echo "ERROR: Xcode Command Line Tools not installed." >&2
        echo "       Run: xcode-select --install" >&2
        exit 1
    fi
    echo "       Using Metal backend (Apple Silicon GPU)"
    CMAKE_FLAGS="-DGGML_METAL=ON"
    BUILD_JOBS="$(sysctl -n hw.logicalcpu)"

elif [[ "${OS}" == "Linux" ]]; then
    # RHEL 9.7 — OpenBLAS, CPU-only
    if ! command -v dnf &>/dev/null; then
        echo "ERROR: dnf package manager not found." >&2
        echo "       This script supports RHEL 9.x / CentOS Stream / Fedora only." >&2
        exit 1
    fi
    echo "       Installing build dependencies via dnf..."
    # Use sudo only if available; user can pre-install deps manually on
    # locked-down machines where sudo is not granted.
    if command -v sudo &>/dev/null; then
        sudo dnf install -y git cmake gcc gcc-c++ make openblas-devel
    else
        dnf install -y git cmake gcc gcc-c++ make openblas-devel
    fi
    CMAKE_FLAGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
    BUILD_JOBS="$(nproc)"

else
    # AC-17: reject unsupported OS
    echo "ERROR: Unsupported OS: ${OS}." >&2
    echo "       Supported platforms: macOS (Apple Silicon) and Linux (RHEL 9.7)." >&2
    exit 1
fi

# ── Step 2: Clone or update ────────────────────────────────────────────────────

echo "[2/4] Fetching llama.cpp source..."

if [[ -d "${BUILD_DIR}/.git" ]]; then
    echo "       Existing clone found at ${BUILD_DIR} — updating..."
    git -C "${BUILD_DIR}" fetch --tags --quiet
    git -C "${BUILD_DIR}" reset --hard HEAD --quiet
    git -C "${BUILD_DIR}" pull --quiet
else
    echo "       Cloning to ${BUILD_DIR}..."
    git clone --depth=1 "${LLAMA_CPP_REPO}" "${BUILD_DIR}"
fi

if [[ -n "${LLAMA_CPP_VERSION}" ]]; then
    echo "       Checking out ${LLAMA_CPP_VERSION}..."
    git -C "${BUILD_DIR}" fetch --tags --quiet
    git -C "${BUILD_DIR}" checkout "${LLAMA_CPP_VERSION}"
fi

# ── Step 3: Build ──────────────────────────────────────────────────────────────

echo "[3/4] Building llama-server (${BUILD_JOBS} parallel jobs)..."
echo "      flags: ${CMAKE_FLAGS}"
echo ""

_cmake -S "${BUILD_DIR}" -B "${BUILD_DIR}/cmake-build" \
    ${CMAKE_FLAGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=ON

_cmake --build "${BUILD_DIR}/cmake-build" \
    --config Release \
    --target llama-server \
    -j"${BUILD_JOBS}"

# ── Step 4: Install ────────────────────────────────────────────────────────────

echo ""
echo "[4/4] Installing to ${INSTALL_PREFIX}/bin..."

# Locate the built binary — path differs slightly between llama.cpp versions
BINARY_PATH=""
for candidate in \
    "${BUILD_DIR}/cmake-build/bin/llama-server" \
    "${BUILD_DIR}/cmake-build/llama-server"; do
    if [[ -x "${candidate}" ]]; then
        BINARY_PATH="${candidate}"
        break
    fi
done

if [[ -z "${BINARY_PATH}" ]]; then
    echo "ERROR: llama-server binary not found after build." >&2
    echo "       Expected locations checked:" >&2
    echo "         ${BUILD_DIR}/cmake-build/bin/llama-server" >&2
    echo "         ${BUILD_DIR}/cmake-build/llama-server" >&2
    find "${BUILD_DIR}/cmake-build" -name "llama-server" 2>/dev/null && true
    exit 1
fi

mkdir -p "${INSTALL_PREFIX}/bin"

# ── Install dylibs (macOS only) ───────────────────────────────────────────────
# llama.cpp now builds several shared libraries (libmtmd, libllama, libggml*).
# The binary embeds an @rpath pointing to the build directory under /tmp, which
# is cleared on reboot. We copy all dylibs next to the binary and fix the rpath.
DYLIB_SRC_DIR="$(dirname "${BINARY_PATH}")"
DYLIB_DEST_DIR="${INSTALL_PREFIX}/bin"

if [[ "${OS}" == "Darwin" ]]; then
    shopt -s nullglob
    DYLIBS=("${DYLIB_SRC_DIR}"/*.dylib)
    shopt -u nullglob
    if [[ ${#DYLIBS[@]} -gt 0 ]]; then
        echo "       Copying ${#DYLIBS[@]} dylib(s) to ${DYLIB_DEST_DIR}..."
        for dylib in "${DYLIBS[@]}"; do
            if [[ -w "${DYLIB_DEST_DIR}" ]]; then
                install -m 755 "${dylib}" "${DYLIB_DEST_DIR}/"
            else
                sudo install -m 755 "${dylib}" "${DYLIB_DEST_DIR}/"
            fi
        done
    fi
fi

# Use sudo only when the prefix is not user-writable (e.g. /usr/local)
if [[ -w "${INSTALL_PREFIX}/bin" ]]; then
    install -m 755 "${BINARY_PATH}" "${INSTALL_PREFIX}/bin/llama-server"
else
    sudo install -m 755 "${BINARY_PATH}" "${INSTALL_PREFIX}/bin/llama-server"
fi

# ── Fix rpath (macOS only) ─────────────────────────────────────────────────────
# Replace the /tmp build-dir rpath with the permanent install directory so the
# binary resolves its @rpath dylibs after reboot.
if [[ "${OS}" == "Darwin" ]] && command -v install_name_tool &>/dev/null; then
    INSTALLED_BIN="${INSTALL_PREFIX}/bin/llama-server"
    # Collect all LC_RPATH entries pointing to the build dir
    OLD_RPATHS=$(otool -l "${INSTALLED_BIN}" 2>/dev/null \
        | awk '/LC_RPATH/{found=1} found && /path /{print $2; found=0}' \
        | grep -v "^${DYLIB_DEST_DIR}$" || true)
    if [[ -n "${OLD_RPATHS}" ]]; then
        echo "       Rewriting rpath: ${OLD_RPATHS} → ${DYLIB_DEST_DIR}"
        while IFS= read -r old_rpath; do
            install_name_tool -rpath "${old_rpath}" "${DYLIB_DEST_DIR}" "${INSTALLED_BIN}" 2>/dev/null || true
        done <<< "${OLD_RPATHS}"
    fi
    # Also fix rpaths in the installed dylibs themselves
    for installed_dylib in "${DYLIB_DEST_DIR}"/*.dylib; do
        OLD_RPATHS=$(otool -l "${installed_dylib}" 2>/dev/null \
            | awk '/LC_RPATH/{found=1} found && /path /{print $2; found=0}' \
            | grep -v "^${DYLIB_DEST_DIR}$" || true)
        if [[ -n "${OLD_RPATHS}" ]]; then
            while IFS= read -r old_rpath; do
                install_name_tool -rpath "${old_rpath}" "${DYLIB_DEST_DIR}" "${installed_dylib}" 2>/dev/null || true
            done <<< "${OLD_RPATHS}"
        fi
    done
fi

# ── Verify ─────────────────────────────────────────────────────────────────────

echo ""
echo "✓ llama-server installed successfully."
echo ""
"${INSTALL_PREFIX}/bin/llama-server" --version 2>&1 | head -1 || true
echo ""
# Remind user to add the install dir to PATH if it's not a standard location
if [[ ":${PATH}:" != *":${INSTALL_PREFIX}/bin:"* ]]; then
    echo "NOTE: Add the install directory to your PATH:"
    echo "       echo 'export PATH=\"${INSTALL_PREFIX}/bin:\$PATH\"' >> ~/.zshrc"
    echo "       source ~/.zshrc"
    echo ""
fi
echo "Next steps:"
echo "  1. Download a model:"
echo "       export PROMETHEUS_MODEL_URL=https://huggingface.co/.../model.gguf"
echo "       export PROMETHEUS_MODEL_DEST=/srv/models/model.gguf"
echo "       bash runtime/scripts/download-model.sh"
echo ""
echo "  2. Start the server:"
echo "       export PROMETHEUS_MODEL_PATH=/srv/models/model.gguf"
echo "       bash runtime/scripts/start-server.sh"
