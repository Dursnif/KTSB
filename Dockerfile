FROM python:3.12-slim

WORKDIR /kaare

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY . .

ENV PYTHONPATH=/kaare

# Version shown in KTSB's web User-Agent etc. — CI passes the git tag (kaare_core/version.py).
ARG KTSB_VERSION=dev
ENV KTSB_VERSION=${KTSB_VERSION}

COPY scripts/docker_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
# --no-proxy-headers: request.client is the real peer; X-Forwarded-For is read only by
# kaare_core/client_ip.py, and only from GUI-trusted proxies (DT03 R6 H1, rule 27)
CMD ["uvicorn", "kaare_api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
