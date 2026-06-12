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
{{- /* Shared Bearer token: every replica MUST use the same one, else each API pod would
       auto-generate its own and clients couldn't authenticate consistently (design/02 §11.2). */}}
- name: MFS_API_TOKEN
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: api-token }
{{- /* The artifact cache is local-filesystem. In a multi-replica topology, mount a shared
       RWX volume at the cache root so artifacts / upload staging / plain-file cat are visible
       to every pod (design/10 §4.3). */}}
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
