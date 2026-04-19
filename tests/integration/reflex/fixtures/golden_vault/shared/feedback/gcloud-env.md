---
name: gcloud-env
description: Load gcloud credentials from environment variables not from a checked-in file
tags:
  - gcp
  - gcloud
  - credentials
aliases:
  - gcp
  - credentials
sources:
  - bots/projA/memory/gcloud-auth.md
  - bots/projB/memory/gcloud-env.md
stability: stable
---
Load gcloud credentials from environment variables at runtime, never from a credentials file checked into the repo. Use google_application_credentials to point gcloud at a path that the deployment environment populates. In kubernetes on gcp, mount a workload-identity service account; in local development, use gcloud auth application-default login and leave the resulting adc file outside the repo. Checking a gcloud service account key into git leaks production credentials the instant the repo is shared, and rotating a leaked gcloud key is painful. Treat every gcloud key like a password.
