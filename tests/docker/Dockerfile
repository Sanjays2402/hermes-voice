# Test image: the exact Home Assistant release we target + its matching test harness.
FROM ghcr.io/home-assistant/home-assistant:2026.10.4
COPY requirements_test.txt /tmp/requirements_test.txt
RUN pip install --no-cache-dir -r /tmp/requirements_test.txt
WORKDIR /repo
ENTRYPOINT []
