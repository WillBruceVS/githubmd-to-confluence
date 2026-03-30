FROM python:3.11-slim

COPY upload_to_confluence.py /upload_to_confluence.py

ENTRYPOINT ["python", "/upload_to_confluence.py"]
CMD []
