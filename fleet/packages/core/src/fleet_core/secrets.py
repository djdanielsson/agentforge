"""Credential storage: Kubernetes Secrets, by reference (SPEC §15, §16).

The control plane stores metadata and hands the *value* to Kubernetes. Values
are never returned by any API in this package — `read_secret` returns key names,
not data, and the only function that reads a value is the one that copies it into
a workspace's own namespace.
"""

from __future__ import annotations

import base64
import logging

from kubernetes import client
from kubernetes.client.exceptions import ApiException

# Accessed through the module, not imported by name: a test substitutes the
# accessor on the module and needs that substitution to be visible here.
from .workspaces import kubernetes_common

log = logging.getLogger(__name__)


def upsert_secret(namespace: str, name: str, data: dict[str, str], labels: dict[str, str]) -> None:
    cluster = kubernetes_common.get_cluster()
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels),
        type="Opaque",
        data={k: base64.b64encode(v.encode()).decode() for k, v in data.items()},
    )
    try:
        cluster.core.create_namespaced_secret(namespace, body)
    except ApiException as exc:
        if exc.status == 409:
            cluster.core.replace_namespaced_secret(name, namespace, body)
        else:
            raise


def delete_secret(namespace: str, name: str) -> None:
    cluster = kubernetes_common.get_cluster()
    try:
        cluster.core.delete_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise


def secret_keys(namespace: str, name: str) -> list[str]:
    """Key names only. This is what the API is allowed to show."""
    cluster = kubernetes_common.get_cluster()
    try:
        found = cluster.core.read_namespaced_secret(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return []
        raise
    return sorted((found.data or {}).keys())


def secret_present(namespace: str, name: str) -> bool:
    cluster = kubernetes_common.get_cluster()
    try:
        cluster.core.read_namespaced_secret(name, namespace)
        return True
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise


def copy_secret(source_namespace: str, name: str, target_namespace: str, target_name: str) -> bool:
    """Copy a secret between namespaces.

    Used to give a workspace the credentials its own project declared: the pod
    resolves them with `secretKeyRef`, so nothing above Kubernetes holds the
    value at rest.
    """
    cluster = kubernetes_common.get_cluster()
    try:
        found = cluster.core.read_namespaced_secret(name, source_namespace)
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=target_name,
            namespace=target_namespace,
            labels=dict(found.metadata.labels or {}),
        ),
        type=found.type,
        data=found.data,
    )
    try:
        cluster.core.create_namespaced_secret(target_namespace, body)
    except ApiException as exc:
        if exc.status == 409:
            cluster.core.replace_namespaced_secret(target_name, target_namespace, body)
        else:
            raise
    return True
