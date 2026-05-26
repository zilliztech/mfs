{{- define "mfs.labels" -}}
app.kubernetes.io/name: mfs
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "mfs.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{/* common env: milvus + metadata + secret-backed tokens */}}
{{- define "mfs.env" -}}
- name: MFS_MILVUS_URI
  value: {{ .Values.search.uri | quote }}
- name: MFS_METADATA_DSN
  value: {{ .Values.metadata.dsn | quote }}
- name: MFS_MILVUS_TOKEN
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: zilliz-token }
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: openai-api-key }
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
