---
name: docker-multistage
description: Use a multistage docker build to keep runtime images small and free of build tools
tags:
  - docker
  - build
  - multistage
aliases:
  - multistage
  - builder
sources:
  - bots/projA/memory/docker-multistage.md
  - bots/projB/memory/docker-build.md
stability: stable
---
Structure every production dockerfile as a multistage build. A builder stage installs compilers and build dependencies and produces the final artefacts; a final runtime stage copies only those artefacts onto a minimal base image. Multistage docker cuts the runtime image size dramatically and removes every compiler from the shipped container. Avoid installing build tools in the runtime stage of a dockerfile just because it is convenient — each extra package widens the attack surface of the docker image. Cache each multistage docker layer deliberately: copy package manifests first, install deps, then copy source.
