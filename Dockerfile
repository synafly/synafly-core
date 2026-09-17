# Official runtime; production operators should pin an approved image digest.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY synafly_lab/ synafly_lab/
COPY scripts/run_edge_daemon.py scripts/run_edge_daemon.py
COPY data/mesh-topology.json data/mesh-topology.json
RUN mkdir -p /app/.local && chown -R 65532:65532 /app
USER 65532:65532
EXPOSE 8545
ENTRYPOINT ["python3", "scripts/run_edge_daemon.py"]
CMD ["--host", "0.0.0.0"]
