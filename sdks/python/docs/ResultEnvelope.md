# ResultEnvelope

One search/grep hit (design/06 §7). Outer shape is stable across connectors; locator + metadata.fields are per-connector but documented.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**source** | **str** | object URI — feed to cat/head/export | 
**content** | **str** | snippet to read | [optional] [default to '']
**score** | **float** | ranking score; &lt;0.5 often unreliable | [optional] 
**locator** | **Dict[str, object]** | per-chunk identity. body/code/document: `{'lines':[start,end]}`; structured (DB row, issue, slack thread): connector PK dict; once-per-object: null. | [optional] 
**metadata** | **Dict[str, object]** | chunk_kind, connector_type, fields, ... | [optional] 

## Example

```python
from mfs_sdk.models.result_envelope import ResultEnvelope

# TODO update the JSON string below
json = "{}"
# create an instance of ResultEnvelope from a JSON string
result_envelope_instance = ResultEnvelope.from_json(json)
# print the JSON string representation of the object
print(ResultEnvelope.to_json())

# convert the object into a dict
result_envelope_dict = result_envelope_instance.to_dict()
# create an instance of ResultEnvelope from a dict
result_envelope_from_dict = ResultEnvelope.from_dict(result_envelope_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


