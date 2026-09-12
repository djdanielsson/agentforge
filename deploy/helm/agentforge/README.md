# AgentForge Helm chart

Deploys the AgentForge control plane into a Kubernetes cluster: the FastAPI API,
the orchestrator worker, the dashboard, and the database they share — plus the
cluster-wide RBAC the control plane needs because every project it manages
becomes its own namespace.

## What it installs

| Object | Purpose |
| --- | --- |
| Deployment + Service `*-api` | FastAPI control plane on port 8000, liveness at `/health` |
| Deployment `*-orchestrator` | the reconcile loop; no Service, it never accepts traffic |
| Deployment + Service `*-web` | nginx serving the dashboard on 8080, proxying `/api` to the API |
| StatefulSet + headless Service `*-postgres` | single-replica Postgres (optional) |
| ConfigMap `*` | non-secret `AGENTFORGE_*` configuration |
| Secret `*` | database URL, bootstrap API key, Postgres password |
| ServiceAccount + ClusterRole + ClusterRoleBinding `*-control-plane` | namespace/pod/PVC/service/NetworkPolicy management and pod exec |
| Ingress | optional, one host routing `/api` to the API and `/` to the dashboard |

There are no custom resources. A project is a plain namespace whose name starts
with the configured prefix; nothing here installs a CRD or an operator.

## Install

The chart refuses to render without a database password, so a default install
cannot ship a blank credential. Supply one of the two database modes:

Bundled Postgres:

```bash
helm upgrade --install agentforge deploy/helm/agentforge \
  --namespace agentforge --create-namespace \
  --set postgres.auth.password='<strong-password>'
```

External database:

```bash
helm upgrade --install agentforge deploy/helm/agentforge \
  --namespace agentforge --create-namespace \
  --set postgres.enabled=false \
  --set externalDatabase.url='postgresql+psycopg://user:pass@host:5432/agentforge'
```

Or point at a Secret you already manage:

```bash
  --set postgres.enabled=false \
  --set externalDatabase.existingSecret=agentforge-db \
  --set externalDatabase.existingSecretKey=database-url
```

Images default to `ghcr.io/agentforge/{api,orchestrator,web}` tagged with the
chart's `appVersion`. Override them per component, or set a digest, before
installing anywhere real:

```bash
  --set api.image.repository=registry.example.com/agentforge/api \
  --set api.image.tag=sha-abc123
```

Keep the password out of your shell history: pass a values file with `-f`, or set
`postgres.auth.password` from a file with `--set-file`.

## Upgrade

```bash
helm upgrade agentforge deploy/helm/agentforge -n agentforge -f my-values.yaml
```

- Image changes roll the API and orchestrator automatically (the pod template
  carries the tag). The web pods do the same.
- The ConfigMap drives the non-secret env; changing it rolls the pods because
  `envFrom` is part of the pod template.
- The `Secret` is re-rendered on every upgrade. Changing the database password
  rolls the API and orchestrator, but **not** the bundled Postgres: a running
  Postgres keeps its existing password until you update it in the database as
  well. Do the database side first.
- `helm uninstall` leaves the Postgres PVC (`data-<release>-postgres-0`) behind.
  Delete it deliberately, not in passing.

## Development manifests

`k8s/` holds the same RBAC as plain YAML for bootstrapping by hand, plus
development manifests that run the API straight from a local checkout. See
`k8s/README.md`.

## Values that matter

### Database

| Value | Default | Notes |
| --- | --- | --- |
| `postgres.enabled` | `true` | The fork in the road. |
| `postgres.auth.password` | `""` | Required when enabled; the chart fails without it. |
| `postgres.auth.username` / `postgres.auth.database` | `agentforge` | |
| `postgres.persistence.enabled` | `true` | `false` uses an emptyDir and loses data on reschedule. |
| `postgres.persistence.size` | `10Gi` | |
| `postgres.persistence.storageClass` | `""` | Empty means the cluster default. |
| `externalDatabase.url` | `""` | Full SQLAlchemy URL, stored in the chart's Secret. |
| `externalDatabase.existingSecret` / `existingSecretKey` | `""` / `database-url` | Use a Secret you manage instead. |

The connection string is built as
`postgresql+psycopg://<user>:<password>@<release>-postgres:5432/<db>`. The
password is interpolated as-is, so it must be URL-safe (`A-Za-z0-9-_.`); use
`externalDatabase.existingSecret` if it cannot be.

### Authentication

| Value | Default | Notes |
| --- | --- | --- |
| `auth.enabled` | `false` | `false` leaves the API open. Set `true` for anything reachable beyond localhost. |
| `auth.bootstrapApiKey` | `""` | Created on first boot; shown once, stored hashed. |
| `auth.existingSecret` / `existingSecretKey` | `""` / `bootstrap-api-key` | Bring your own Secret for the key. |

### RBAC

| Value | Default | Notes |
| --- | --- | --- |
| `rbac.create` | `true` | Creates the ClusterRole and ClusterRoleBinding. |
| `serviceAccount.create` | `true` | |
| `serviceAccount.name` | `""` | Defaults to `<release>-agentforge-control-plane`. |

The ClusterRole is cluster-wide because the namespaces it manages do not exist
when the role is written. It grants `namespaces`, `pods`, `pods/exec`,
`pods/log`, `persistentvolumeclaims`, `services`, `events`, **read-only**
`secrets`, and `networking.k8s.io/networkpolicies`. The `secrets` grant is `get`
only: workspace secrets are referenced rather than copied, so before a pod starts
the controller verifies the referenced Secret exists in the project namespace.
If your platform forbids cluster-wide roles, pre-create the namespaces your
projects will use and replace this with a Role per namespace.

### Application configuration

| Value | Default | Notes |
| --- | --- | --- |
| `config.environment` | `production` | |
| `config.logLevel` | `INFO` | |
| `config.corsOrigins` | `[]` | Leave empty when the dashboard and API share an Ingress origin. |
| `config.workspaceNamespacePrefix` | `af` | Projects become `<prefix>-<project>`. Avoid changing it after install; it orphans namespaces. |
| `config.workspaceProvider` | `kubernetes` | The in-cluster provider. `podman`/`local` are laptop-only. |
| `config.workspaceImage` | `ghcr.io/coder/code-server:latest` | |
| `config.workspaceAgentImage` | `ghcr.io/all-hands-ai/openhands:latest` | Agent runtime alongside code-server. |
| `config.workspaceStorage` | `10Gi` | Per-project PVC. |
| `config.workspaceStorageClass` | `""` | Empty means the cluster default. |
| `config.openhandsUrl` | `http://openhands.agentforge.svc.cluster.local:3000` | |
| `config.llmGatewayUrl` | `http://agentforge-llm:4000` | Service this chart ships when `llm.enabled`; point it elsewhere for an external gateway. |
| `config.defaultAgentModel` | `local-coder` | |

### Replicas, resources, probes, ingress

Each of `api`, `orchestrator` and `web` has `replicaCount`, `image`,
`resources`, and its own `probes` block. The orchestrator leases one task at a
time, so extra replicas are safe but only help once a single poller saturates.

| Value | Default | Notes |
| --- | --- | --- |
| `ingress.enabled` | `false` | |
| `ingress.className` / `ingress.annotations` | `""` / `{}` | |
| `ingress.host` | `agentforge.example.com` | Required when enabled. |
| `ingress.tls` | `[]` | e.g. `- secretName: agentforge-tls`. |

Ingress sends `/api` to the API Service and `/` to the dashboard, so the browser
sees a single origin and CORS does not apply.

### Model gateway

`llm.enabled` ships a LiteLLM gateway and, optionally, a bundled Ollama for the
local alias. Every alias an agent may store in its `model` column is one entry in
`llm.models`, so adding a model is a values change rather than a code change.

| Value | Default | Notes |
| --- | --- | --- |
| `llm.enabled` | `false` | Off means an external gateway; `config.llmGatewayUrl` must point at it. |
| `llm.existingSecret` | `agentforge-llm` | Must carry `LITELLM_MASTER_KEY` and every provider key the aliases name. |
| `llm.models[].name` | — | The alias an agent stores. |
| `llm.models[].model` | — | Upstream model id, provider-prefixed (`openai/...` also covers Ollama, vLLM, LM Studio). |
| `llm.models[].api_base` | — | OpenAI-compatible base URL; omit for a hosted provider. |
| `llm.models[].api_key_env` | — | Env var in the Secret holding the key. |
| `llm.models[].api_key` | — | Literal key for an endpoint that needs none (a local server ignores it). |
| `llm.ollama.enabled` | `false` | Bundled model server for a local alias. |
| `llm.ollama.model` | `qwen2.5-coder:7b` | Pulled on first use into a PVC. |

An alias whose `api_key_env` is empty in the Secret stays configured but fails
authentication, which is what you want for a provider you have not paid for yet.

```yaml
# A LAN model for the cheap alias, a hosted one for the smart alias.
llm:
  enabled: true
  models:
    - name: local-coder
      model: openai/qwen2.5-coder:7b
      api_base: http://ollama.lan:11434/v1
      api_key: "ollama"
    - name: smart
      model: anthropic/claude-sonnet-4-5
      api_key_env: ANTHROPIC_API_KEY
```

### Security context

`podSecurityContext` (`runAsNonRoot: true`, seccomp `RuntimeDefault`) and
`securityContext` (no privilege escalation, all capabilities dropped) apply to
the API, orchestrator and web pods. They assume the images declare a non-root
`USER`; set `podSecurityContext.runAsNonRoot=false` if you build root-only
images. `securityContext.readOnlyRootFilesystem` is off by default because it
needs image support — turn it on once your images are ready. Postgres has its own
context at `postgres.podSecurityContext` (`fsGroup: 70`, matching
`postgres:16-alpine`).
