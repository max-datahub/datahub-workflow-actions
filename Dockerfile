# Derived-image path. Match DATAHUB_ACTIONS_TAG to the DataHub version the cluster runs; the -slim
# variant is enough (no ingestion sources are needed).
ARG DATAHUB_ACTIONS_TAG=v1.7.0.1-slim
FROM acryldata/datahub-actions:${DATAHUB_ACTIONS_TAG}
COPY --chown=datahub:datahub pyproject.toml README.md LICENSE /tmp/pkg/
COPY --chown=datahub:datahub datahub_workflow_actions /tmp/pkg/datahub_workflow_actions
RUN pip install --no-cache-dir /tmp/pkg && rm -rf /tmp/pkg
