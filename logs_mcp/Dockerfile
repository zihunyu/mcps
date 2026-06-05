FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY mcp ./mcp
COPY center ./center
COPY agent ./agent
COPY run.py ./
COPY center_run.py ./
COPY agent_run.py ./

RUN pip install --no-cache-dir .

ENV LOG_MCP_CONFIG=/app/mcp/config.yaml

CMD ["python", "run.py"]
