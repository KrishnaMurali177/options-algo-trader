FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git && \
    rm -rf /var/lib/apt/lists/*

COPY options_agent/requirements.txt .
RUN pip install --no-cache-dir pandas_ta && \
    grep -v 'pandas.ta' requirements.txt > req_filtered.txt && \
    pip install --no-cache-dir -r req_filtered.txt && \
    rm req_filtered.txt

COPY options_agent/ .

EXPOSE 8501 8000

CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
