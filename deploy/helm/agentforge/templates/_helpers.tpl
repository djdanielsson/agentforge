{{/*
Naming helpers follow the standard Helm convention: `.Values.nameOverride` and
`.Values.fullnameOverride` win, otherwise names are derived from the release and
the chart name. Standard helpers make `helm inspect` and other tooling work.
*/}}
{{- define "agentforge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentforge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "agentforge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels applied to every object. */}}
{{- define "agentforge.labels" -}}
helm.sh/chart: {{ include "agentforge.chart" . }}
{{ include "agentforge.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agentforge
{{- end -}}

{{/*
Selector labels. These are immutable once an object exists, which is why they
are kept separate from the full label set: adding a label later must not change
a Deployment's selector.
*/}}
{{- define "agentforge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Full labels for one component. Call with the root context so helpers can reach
Chart and Release:
  {{ include "agentforge.componentLabels" (dict "root" $ "component" "api") }}
*/}}
{{- define "agentforge.componentLabels" -}}
{{ include "agentforge.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Selector labels for one component; must be a subset of componentLabels. */}}
{{- define "agentforge.componentSelectorLabels" -}}
{{ include "agentforge.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Per-component object names. */}}
{{- define "agentforge.api.fullname" -}}
{{- printf "%s-api" (include "agentforge.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentforge.orchestrator.fullname" -}}
{{- printf "%s-orchestrator" (include "agentforge.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentforge.web.fullname" -}}
{{- printf "%s-web" (include "agentforge.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentforge.postgres.fullname" -}}
{{- printf "%s-postgres" (include "agentforge.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Shared ServiceAccount, used by both the API and the orchestrator. */}}
{{- define "agentforge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (printf "%s-control-plane" (include "agentforge.fullname" .)) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Secret plumbing.

The chart owns one Secret named after the release unless the operator points at
an existing one. That Secret always carries the bootstrap API key; it carries the
database URL only when the chart is the one that knows it (bundled Postgres or an
externalDatabase.url supplied in values).
*/}}
{{- define "agentforge.secretName" -}}
{{- include "agentforge.fullname" . -}}
{{- end -}}

{{- define "agentforge.databaseSecretName" -}}
{{- if and (not .Values.postgres.enabled) .Values.externalDatabase.existingSecret -}}
{{- .Values.externalDatabase.existingSecret -}}
{{- else if and .Values.postgres.enabled .Values.postgres.auth.existingSecret -}}
{{- .Values.postgres.auth.existingSecret -}}
{{- else -}}
{{- include "agentforge.fullname" . -}}
{{- end -}}
{{- end -}}

{{/* The Secret holding POSTGRES_PASSWORD. Separate from the database URL key
because an operator-supplied Secret uses a different key name for each. */}}
{{- define "agentforge.postgresPasswordSecretName" -}}
{{- if and .Values.postgres.enabled .Values.postgres.auth.existingSecret -}}
{{- .Values.postgres.auth.existingSecret -}}
{{- else -}}
{{- include "agentforge.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "agentforge.databaseSecretKey" -}}
{{- if and (not .Values.postgres.enabled) .Values.externalDatabase.existingSecret -}}
{{- .Values.externalDatabase.existingSecretKey -}}
{{- else -}}
database-url
{{- end -}}
{{- end -}}

{{- define "agentforge.bootstrapSecretName" -}}
{{- if .Values.auth.existingSecret -}}
{{- .Values.auth.existingSecret -}}
{{- else -}}
{{- include "agentforge.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "agentforge.bootstrapSecretKey" -}}
{{- if .Values.auth.existingSecret -}}
{{- .Values.auth.existingSecretKey -}}
{{- else -}}
bootstrap-api-key
{{- end -}}
{{- end -}}

{{/*
The connection string the API and orchestrator use.

SQLAlchemy needs the driver-qualified URL. The password is interpolated raw, so
it must be URL-safe (alphanumerics, "-", "_", "."); anything else must be
percent-encoded or supplied through externalDatabase.existingSecret.
*/}}
{{- define "agentforge.databaseUrl" -}}
{{- if .Values.postgres.enabled -}}
{{- $p := .Values.postgres -}}
{{/*
`required` is assigned, never printed: using it as an output statement would
emit the password into the rendered Secret before the URL.
*/}}
{{- $password := required "postgres.auth.password is required when postgres.enabled=true (or set postgres.enabled=false and configure externalDatabase)" $p.auth.password -}}
{{- printf "postgresql+psycopg://%s:%s@%s:%v/%s" $p.auth.username $password (include "agentforge.postgres.fullname" .) $p.service.port $p.auth.database -}}
{{- else -}}
{{- $url := required "externalDatabase.url is required when postgres.enabled=false and externalDatabase.existingSecret is not set" .Values.externalDatabase.url -}}
{{- printf "%s" $url -}}
{{- end -}}
{{- end -}}

{{/*
Environment shared by the API and the orchestrator.

Non-secret configuration comes from the ConfigMap as a block; the two secrets
(database URL, bootstrap key) are injected individually so a missing optional key
cannot take the whole pod down at once.
*/}}
{{- define "agentforge.controlPlaneEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "agentforge.fullname" . }}
env:
  - name: AGENTFORGE_DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: {{ include "agentforge.databaseSecretName" . }}
        key: {{ include "agentforge.databaseSecretKey" . }}
  - name: AGENTFORGE_BOOTSTRAP_API_KEY
    valueFrom:
      secretKeyRef:
        name: {{ include "agentforge.bootstrapSecretName" . }}
        key: {{ include "agentforge.bootstrapSecretKey" . }}
        # Optional so an operator-managed Secret without this key still boots.
        optional: true
  {{- if .Values.llm.enabled }}
  # The control plane is a gateway client too: it asks the gateway which aliases
  # exist, and a gateway that authenticates its callers wants the same key the
  # agents present.
  - name: AGENTFORGE_LLM_GATEWAY_API_KEY
    valueFrom:
      secretKeyRef:
        name: {{ required "llm.existingSecret is required when llm.enabled" .Values.llm.existingSecret }}
        key: LITELLM_MASTER_KEY
  {{- end }}
{{- end -}}
