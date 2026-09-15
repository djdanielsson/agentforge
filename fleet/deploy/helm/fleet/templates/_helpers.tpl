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
