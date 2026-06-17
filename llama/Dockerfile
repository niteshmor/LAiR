# Multi-stage build: compile llama.cpp from a pinned release tag with CUDA, then
# ship just the binary + CUDA runtime.

# CUDA 13.x requires a recent NVIDIA host driver (R580+). If `nvidia-smi` on
# the host shows an older driver, either upgrade it or override at build
# time:  docker compose build --build-arg CUDA_VERSION=12.6.0 llama
ARG CUDA_VERSION=13.1.2
ARG UBUNTU_VERSION=24.04

FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The CUDA devel image ships a `libcuda.so` stub for link-time use (the real
# libcuda.so.1 is provided at runtime by the host driver via the NVIDIA
# Container Toolkit). Two problems to work around at build time:
#   1. The stub lacks the soname-versioned filename `libcuda.so.1`, but
#      `libggml-cuda.so` declares a DT_NEEDED on that exact name.
#   2. ld's TRANSITIVE shared-lib lookup ignores LIBRARY_PATH/`-L`; it uses
#      rpath-link, rpath, LD_LIBRARY_PATH, or ld.so.conf. So we have to
#      tell ld about the stubs dir specifically, not just gcc.
RUN ln -sf /usr/local/cuda/lib64/stubs/libcuda.so \
           /usr/local/cuda/lib64/stubs/libcuda.so.1
ENV LIBRARY_PATH=/usr/local/cuda/lib64/stubs
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64/stubs

WORKDIR /build
# Pinned to release b9354 (commit 9777256) — the build benchmarked and verified
# for tool-call parser behaviour. Changing this re-rolls the llama.cpp tool-call
# parser regressions (see the tool-calling notes in BENCHMARKS.md and the .env
# block comments): bump deliberately, then re-run the benchmarks before trusting it.
RUN git clone --depth 1 --branch b9354 https://github.com/ggml-org/llama.cpp.git

WORKDIR /build/llama.cpp
RUN cmake -B build \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES=89 \
        -DLLAMA_CURL=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
        -DCMAKE_SHARED_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
    && cmake --build build --config Release -j16 --target llama-server

# Collect everything we need to copy: the binary plus any shared libs it
# built alongside it (ggml etc.).
RUN mkdir -p /out/bin /out/lib \
    && cp build/bin/llama-server /out/bin/ \
    && find build -name '*.so' -exec cp {} /out/lib/ \;


FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/bin/llama-server /usr/local/bin/llama-server
COPY --from=builder /out/lib/ /usr/local/lib/
RUN ldconfig

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
