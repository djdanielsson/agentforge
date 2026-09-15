{{- define "fleet.labels" -}}
app.kubernetes.io/name: fleet
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: control-plane
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "fleet.fullname" -}}
{{ .Release.Name }}-api
{{- end -}}

{{- define "fleet.apiUrl" -}}
{{- default (printf "http://%s.%s.svc.cluster.local:8000" (include "fleet.fullname" .) .Values.namespace) .Values.controlPlaneUrl -}}
{{- end -}}

{{- define "fleet.mcpUrl" -}}
{{- default (printf "%s/mcp" (include "fleet.apiUrl" .)) .Values.mcp.url -}}
{{- end -}}

{{- define "fleet.t3Url" -}}
{{- default (printf "https://%s-%s-ingress.%s" .Values.namespace .Values.t3.ingressName .Values.tailnetDomain) .Values.t3.url -}}
{{- end -}}
