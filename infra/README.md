# infra

Infrastructure-as-code for deploying the fleet copilot pipeline.

Nothing is provisioned yet. When it is, everything that creates cloud
resources belongs here rather than in application code, so that the
deployment topology is reviewable in the same pull request as the
behaviour it supports.
