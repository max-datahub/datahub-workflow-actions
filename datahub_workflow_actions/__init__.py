"""DataHub Action that applies catalog side effects when Action Workflow requests complete.

Flat package on purpose: every module sits at this level so the directory can be mounted from a
single Kubernetes ConfigMap onto PYTHONPATH. Do not add sub-packages.
"""

__version__ = "0.1.0"
