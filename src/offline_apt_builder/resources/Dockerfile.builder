ARG BASE_IMAGE
FROM ${BASE_IMAGE}
SHELL ["/bin/bash", "-Eeuo", "pipefail", "-c"]
COPY ubuntu.sources /tmp/ubuntu.sources
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
    && cp /tmp/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o APT::Update::Error-Mode=any update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 python3-apt dpkg-dev ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
COPY resolve.py apt_compatibility.py /opt/offline-apt/
ENTRYPOINT ["python3", "/opt/offline-apt/resolve.py"]
