# IaC

Engine-specific templates, generators, and shared helpers.

## Conventions
- One folder per IaC engine (e.g., `Bicep`, `Terraform`, `OpenTofu`).
- Engine logic here should remain cloud-agnostic where possible.
- Project-specific generated artifacts belong under `Projects/Default/IaC/`.
