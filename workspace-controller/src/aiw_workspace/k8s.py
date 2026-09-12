"""Kubernetes client construction.

Works in-cluster (service account) or out-of-cluster (kubeconfig file). We never
fall back to a default kubeconfig silently in production.
"""

from __future__ import annotations

import logging

from aiw_shared.config import Settings, get_settings
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

log = logging.getLogger(__name__)


class KubernetesUnavailable(RuntimeError):
    """Raised when no usable cluster credentials are present."""


def load_client(settings: Settings | None = None) -> client.ApiClient:
    settings = settings or get_settings()
    try:
        if settings.k8s_in_cluster:
            config.load_incluster_config()
            log.info("kubernetes: using in-cluster service account")
        else:
            config.load_kube_config(config_file=settings.kubeconfig or None)
            log.info("kubernetes: using kubeconfig %s", settings.kubeconfig or "(default)")
    except ConfigException as exc:
        raise KubernetesUnavailable(
            "no Kubernetes credentials found. Set AIW_K8S_IN_CLUSTER=true inside a "
            "pod, or point AIW_KUBECONFIG at a kubeconfig."
        ) from exc
    return client.ApiClient()


def core(cfg: client.ApiClient) -> client.CoreV1Api:
    return client.CoreV1Api(cfg)
