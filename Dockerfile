ARG PYTHON_BASE=python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
FROM ${PYTHON_BASE} AS native-builder
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential cmake pkg-config libopenblas-dev git gnupg ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'setuptools>=77' wheel 'cython>=3.1,<4' pybind11 meson-python meson ninja
COPY tools/collect_licenses.py /build/collect_licenses.py
RUN python /build/collect_licenses.py /release-sources/build-dependency-notices
COPY tools/fetch_native_sources.py /build/fetch_native_sources.py
COPY third_party/native-lock.json /build/native-lock.json
COPY docker/build-native.sh /build/build-native.sh
RUN bash /build/build-native.sh

FROM scratch AS native-sources
COPY --from=native-builder /release-sources /

FROM ${PYTHON_BASE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ZILCHO_CONFIG=/config ZILCHO_MEDIA=/media ZILCHO_BIND=0.0.0.0 ZILCHO_PORT=8765 HOME=/config PATH=/opt/zilcho-native/bin:$PATH LD_LIBRARY_PATH=/opt/zilcho-native/lib
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libopenblas0-pthread libgomp1 gosu ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY --from=native-builder /opt/zilcho-native /opt/zilcho-native
COPY --from=native-builder /wheels /wheels
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY zilcho_subs ./zilcho_subs
# Native AV/CT2 wheels are controlled builds; whole-cue Argos-format translation needs no neural sentence-segmentation runtime.
RUN pip install --no-cache-dir /wheels/*.whl && pip install --no-cache-dir '.[inference]' --report /tmp/app-install.json
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/zilcho-subs/
COPY third_party/upstream-notices /usr/share/zilcho-subs/upstream-notices
COPY --from=native-builder /release-sources/build /usr/share/zilcho-subs/native-build
COPY --from=native-builder /release-sources/build-dependency-notices /usr/share/zilcho-subs/build-dependency-notices
COPY tools/collect_licenses.py /usr/local/lib/zilcho-subs-collect-licenses.py
RUN python /usr/local/lib/zilcho-subs-collect-licenses.py /usr/share/zilcho-subs/dependencies && mv /tmp/app-install.json /usr/share/zilcho-subs/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "zilcho_subs"]
