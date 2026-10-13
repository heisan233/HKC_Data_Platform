FROM python:3.11

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 lxp && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash lxp

ENV HOME=/home/lxp

USER lxp

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
