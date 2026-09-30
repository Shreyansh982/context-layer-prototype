FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
# PyPI's Linux Torch wheel is CUDA-enabled.  Install the pinned CPU build from
# PyTorch's official CPU index before installing Sentence Transformers.
RUN pip install --no-cache-dir --timeout 300 \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.13.0+cpu \
    && pip install --no-cache-dir --timeout 300 -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY data/company_policies.md ./data/company_policies.md
COPY dashboard ./dashboard

EXPOSE 8000
