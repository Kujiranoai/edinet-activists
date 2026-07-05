FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g firebase-tools

COPY pyproject.toml README.md ./
COPY edinet_watcher ./edinet_watcher
COPY activists.yml prompt_extract.md prompt_article.md prompt_followup.md ./

RUN pip install --no-cache-dir .

ENTRYPOINT ["edinet-watch"]
CMD ["--help"]
