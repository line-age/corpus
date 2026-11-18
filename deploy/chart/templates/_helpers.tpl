{{- define "corpus.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "corpus.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "corpus.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Kept by Helm on uninstall and never pruned or deleted by Argo CD: losing these loses data. */}}
{{- define "corpus.keep" -}}
helm.sh/resource-policy: keep
argocd.argoproj.io/sync-options: Delete=false,Prune=false
{{- end -}}

{{/* The secret holding a database's connection keys: the CNPG app secret, or an external one. */}}
{{- define "corpus.dbSecret" -}}
{{- $db := index .root.Values.database .name -}}
{{- $db.existingSecret | default (printf "%s-%s-db-app" (include "corpus.fullname" .root) .name) -}}
{{- end -}}

{{- define "corpus.claim" -}}
{{- .Values.rawStorage.existingClaim | default (printf "%s-raw" (include "corpus.fullname" .)) -}}
{{- end -}}

{{/* POSTGRES_* for the API and Alembic, from the API database's secret. */}}
{{- define "corpus.apiDbEnv" -}}
{{- $s := include "corpus.dbSecret" (dict "root" . "name" "api") }}
- name: POSTGRES_HOST
  valueFrom: {secretKeyRef: {name: {{ $s }}, key: host}}
- name: POSTGRES_PORT
  valueFrom: {secretKeyRef: {name: {{ $s }}, key: port}}
- name: POSTGRES_DB
  valueFrom: {secretKeyRef: {name: {{ $s }}, key: dbname}}
- name: POSTGRES_USER
  valueFrom: {secretKeyRef: {name: {{ $s }}, key: username}}
- name: POSTGRES_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ $s }}, key: password}}
- name: PGSSLMODE
  value: {{ .Values.database.sslmode | quote }}
{{- end -}}

{{/* INGEST_DB_URI, a postgresql:// URI; corpus.ingestUrl turns it into SQLAlchemy's form. */}}
{{- define "corpus.ingestDbEnv" -}}
- name: INGEST_DB_URI
  valueFrom: {secretKeyRef: {name: {{ include "corpus.dbSecret" (dict "root" . "name" "ingest") }}, key: uri}}
{{- end -}}

{{/* Shell: the ingest database as SQLAlchemy wants it, with psycopg 3 as the driver. */}}
{{- define "corpus.ingestUrl" -}}
postgresql+psycopg://${INGEST_DB_URI#postgresql://}
{{- end -}}

{{- define "corpus.envMap" -}}
{{- range $k, $v := . }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "corpus.envFrom" -}}
{{- with . }}
envFrom:
  {{- range . }}
  - secretRef: {name: {{ . }}}
  {{- end }}
{{- end }}
{{- end -}}
