# Troubleshooting local development

Common failure modes when running hermes-station locally or under CI.

---

## Container won't start

**Symptom:** `container run` / `docker run` exits immediately or `curl http://127.0.0.1:8787/health` never returns 200.

**Likely cause:** Missing required env vars, port conflict, or an image build error.

**Fix:**

1. Check the container logs for the error message:

   ```bash
   # Apple container
   container logs hs-test

   # Docker
   docker logs hs-test
   ```

2. Confirm the required env var is set (`HERMES_WEBUI_PASSWORD`).

3. Check for a port conflict. If something else is already on `8787`:

   ```bash
   lsof -i :8787
   ```

   Change the host port mapping (e.g. `-p 8788:8787`) and update `HERMES_STATION_E2E_URL` to match.

4. Verify the image built successfully:

   ```bash
   # Apple container
   container images | grep hermes-station

   # Docker
   docker images | grep hermes-station
   ```

---

## E2E tests time out on the readiness probe

**Symptom:** Tests fail with a connection error or timeout against `http://127.0.0.1:8787`.

**Likely cause:** The container is not running, is still booting, or `HERMES_STATION_E2E_URL` points to the wrong address.

**Fix:**

1. Confirm the container is running:

   ```bash
   # Apple container
   container list

   # Docker
   docker ps
   ```

2. Poll the health endpoint manually before running tests:

   ```bash
   curl -s http://127.0.0.1:8787/health | jq .status
   ```

   Expected: `"ok"`. If nothing responds, the container is not booted or the port mapping is wrong.

3. Make sure `HERMES_STATION_E2E_URL` matches the port you mapped. If you used `-p 8788:8787`, set:

   ```bash
   HERMES_STATION_E2E_URL=http://127.0.0.1:8788
   ```

---

## `host.containers.internal` doesn't resolve (Apple container CLI)

**Symptom:** In-container tests fail to reach the host with a DNS resolution error for `host.containers.internal`.

**Likely cause:** The Apple `container` CLI does not inject `host.containers.internal` into container DNS, unlike Docker Desktop.

**Fix:** Use `192.168.64.1` instead. This is the host address reachable from inside Apple `container` CLI containers when the host is listening on `0.0.0.0`:

```bash
container run --rm \
  -e HERMES_STATION_E2E_URL=http://192.168.64.1:8787 \
  ...
  hermes-station:test \
  python -m pytest tests/test_container_toolbelt.py tests/test_plugin_manifests.py -v --no-cov
```

---

## `uv pip install` fails with EUCLEAN

**Symptom:** `uv pip install` inside a Dockerfile fails with an error like `EUCLEAN: filesystem state is unexpected (os error 117)` or similar hardlink errors.

**Likely cause:** Apple `container` CLI uses VirtioFS to share the filesystem between the host and the container. VirtioFS does not support hardlinks, and `uv` defaults to hardlink mode when installing packages.

**Fix:** Add `--link-mode=copy` to every `uv pip install` call in the Dockerfile:

```dockerfile
RUN uv pip install --link-mode=copy ...
```

This is already applied in the project's `Dockerfile`. If you see this error in a custom build stage or a local venv inside a container, add the flag explicitly.
