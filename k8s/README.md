# Plain Kubernetes manifests for bootstrapping AgentForge by hand.
#
# These are the non-Helm path: apply them in order, and keep them in step with
# the chart when the controller's Kubernetes API calls change.
#
#   kubectl apply -f k8s/namespaces/
#   kubectl apply -f k8s/rbac/
#   kubectl apply -f k8s/development/   # optional local control plane
#
# Layout
#   namespaces/   the control-plane namespace (workspace namespaces are created
#                 at runtime by the controller, one per project)
#   rbac/         the cluster role, cluster role binding and ServiceAccount the
#                 API and orchestrator run as
#   development/  a throwaway API plus Postgres running inside k3s against a
#                 local source checkout
#
# Verify the permissions after applying:
#
#   kubectl auth can-i create namespaces \
#     --as=system:serviceaccount:agentforge:agentforge-control-plane
#   kubectl auth can-i create pods/exec \
#     --as=system:serviceaccount:agentforge:agentforge-control-plane
#
# The Helm chart in deploy/helm/agentforge is the supported way to install the
# control plane itself; it creates the same RBAC under release-scoped names.
