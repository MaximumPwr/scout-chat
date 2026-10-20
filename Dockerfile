FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Only the MCP server's dependencies; the Streamlit app is not deployed here.
COPY requirements-mcp.txt .
RUN pip install --no-cache-dir -r requirements-mcp.txt

# Only the files the MCP server needs.
COPY river_scout_api.py mcp_server.py ./

# Run as an unprivileged user.
RUN useradd --create-home appuser
USER appuser

# Cloud Run sets PORT (default 8080); mcp_server.py reads it.
EXPOSE 8080
CMD ["python", "mcp_server.py"]
