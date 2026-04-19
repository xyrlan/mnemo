---
name: docker-entrypoint
description: Use exec form entrypoint in docker so signals reach the container process
tags:
  - docker
  - containers
aliases:
  - entrypoint
  - signals
sources:
  - bots/projA/memory/docker-entrypoint.md
  - bots/projB/memory/docker-signals.md
stability: stable
---
Write the docker entrypoint in exec form, not shell form. Exec form runs the process as pid 1 directly, so sigterm from docker stop reaches your application and it can shut down gracefully. Shell form wraps the process in a shell, which swallows signals and forces the container into the ten second kill timeout. If you need shell features like variable expansion in the entrypoint, use a small exec wrapper script that ends with exec so the final process replaces the shell. A container that ignores sigterm breaks rolling deploys and makes every docker restart slower than it needs to be.
