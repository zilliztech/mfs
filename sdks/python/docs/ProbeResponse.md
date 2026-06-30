# ProbeResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**target** | **str** |  | 
**type** | **str** |  | 
**ok** | **bool** |  | 
**detail** | **str** |  | [optional] [default to '']

## Example

```python
from mfs_sdk.models.probe_response import ProbeResponse

# TODO update the JSON string below
json = "{}"
# create an instance of ProbeResponse from a JSON string
probe_response_instance = ProbeResponse.from_json(json)
# print the JSON string representation of the object
print(ProbeResponse.to_json())

# convert the object into a dict
probe_response_dict = probe_response_instance.to_dict()
# create an instance of ProbeResponse from a dict
probe_response_from_dict = ProbeResponse.from_dict(probe_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


