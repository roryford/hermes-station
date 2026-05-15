---
name: Bug report
about: Something isn't working
labels: bug
---

**Describe the bug**
A clear description of what happened and what you expected instead.

**Steps to reproduce**
1. 
2. 
3. 

**Environment**
- hermes-station version (image tag or from `/health`):
- hermes-agent version (from `/health` or image build args):
- Deployment: Railway / local Docker / Apple container

**Logs**
Relevant JSON log lines. Filter with `jq` to keep it focused:
```bash
# Docker
docker logs hermes-station | jq 'select(.level=="error" or .level=="warning")'
# Apple container
container logs hermes-station | jq 'select(.level=="error" or .level=="warning")'
```

```
paste logs here
```

**Additional context**
