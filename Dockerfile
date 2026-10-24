# vog-desk: the forecast desk in one container.
#   docker build -t vog-desk .
#   docker run --rm -e SAMPLE_MODE=true vog-desk --issue 2025-09-02          # no keys, recorded model replies
#   docker run --rm --env-file .env --network zetaris_default vog-desk --issue 2025-09-02   # live: Zetaris, Nightshift on the host
# The image carries the code, the frozen model export and the recorded samples. Raw and derived data are
# rebuilt by scripts/ and mounted or copied in; SAMPLE_MODE needs only data/derived (see README).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[model,agents]"
COPY run.py api.py ./
COPY scripts ./scripts
COPY models ./models
COPY samples ./samples
COPY data/derived ./data/derived

ENV SAMPLE_MODE=true
EXPOSE 8000
ENTRYPOINT ["python", "run.py"]
CMD ["--issue", "2025-09-02"]
