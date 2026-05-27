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
{{- if eq .Values.objectStore.type "s3" }}
{{- /* Without these the server falls back to a LOCAL object store, so artifacts / upload
       staging / plain-file cat would split per pod across replicas (design/10 §4.3). */}}
- name: MFS_OBJECT_STORE_BUCKET
  value: {{ .Values.objectStore.bucket | quote }}
- name: MFS_OBJECT_STORE_ENDPOINT
  value: {{ .Values.objectStore.endpoint | quote }}
- name: MFS_OBJECT_STORE_REGION
  value: {{ .Values.objectStore.region | default "" | quote }}
- name: MFS_OBJECT_STORE_PREFIX
  value: {{ .Values.objectStore.prefix | default "" | quote }}
- name: MFS_OBJECT_STORE_ACCESS_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: object-store-access-key }
- name: MFS_OBJECT_STORE_SECRET_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: object-store-secret-key }
{{- end }}
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
