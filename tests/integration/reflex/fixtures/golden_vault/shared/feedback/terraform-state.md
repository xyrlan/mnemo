---
name: terraform-state
description: Store terraform state in a remote backend with locking enabled
tags:
  - terraform
  - infrastructure
  - state
aliases:
  - tfstate
  - backend
sources:
  - bots/projA/memory/terraform-backend.md
  - bots/projB/memory/terraform-state.md
stability: stable
---
Terraform state must live in a remote backend with state locking turned on. A terraform backend like s3 plus dynamodb or gcs with object locks prevents two operators from running terraform apply at the same time and corrupting the terraform state file. A local terraform state file on a laptop is a single point of failure: lose the laptop and you lose the mapping between terraform resources and real cloud infrastructure. Never commit terraform state to git — it often contains secrets. Rotate the terraform backend credentials and encrypt the terraform state at rest.
