

# ResultEnvelope

One search/grep hit (design/06 §7). Outer shape is stable across connectors; locator + metadata.fields are per-connector but documented.

## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**source** | **String** | object URI — feed to cat/head/export |  |
|**lines** | **List&lt;Integer&gt;** | [start,end] for text/code; null for structured |  [optional] |
|**content** | **String** | snippet to read |  [optional] |
|**score** | **BigDecimal** | ranking score; &lt;0.5 often unreliable |  [optional] |
|**locator** | **Map&lt;String, Object&gt;** | structured unit key (pk/number/thread_ts) |  [optional] |
|**metadata** | **Map&lt;String, Object&gt;** | chunk_kind, connector_type, fields, ... |  [optional] |



