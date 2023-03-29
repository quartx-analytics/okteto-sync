FROM okteto/okteto:latest as okteto

FROM python:alpine
COPY entrypoint.py /entrypoint.py
RUN chmod +x /entrypoint.py
COPY --from=okteto /usr/local/bin/okteto /usr/local/bin/okteto

ENTRYPOINT ["/entrypoint.py"]
