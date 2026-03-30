FROM python:3.11-slim

# Install dependencies
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY upload_to_confluence.py /upload_to_confluence.py

ENTRYPOINT ["python", "/upload_to_confluence.py"]
CMD []
