FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV TZ=America/New_York

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git tzdata && \
    rm -rf /var/lib/apt/lists/*

COPY options_agent/requirements.txt .
RUN pip install --no-cache-dir pandas_ta && \
    grep -v 'pandas.ta' requirements.txt > req_filtered.txt && \
    pip install --no-cache-dir -r req_filtered.txt && \
    rm req_filtered.txt

COPY options_agent/ .

HEALTHCHECK --interval=120s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import json,time; h=json.load(open('/tmp/agent_heartbeat')); age=time.time()-__import__('datetime').datetime.fromisoformat(h['ts']).timestamp(); exit(0 if age<600 else 1)" || exit 1

EXPOSE 8501 8000

CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
