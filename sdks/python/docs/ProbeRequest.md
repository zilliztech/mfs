# ProbeRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**target** | **str** |  | 
**config** | **Dict[str, object]** |  | [optional] 

## Example

```python
from mfs_sdk.models.probe_request import ProbeRequest

# TODO update the JSON string below
json = "{}"
# create an instance of ProbeRequest from a JSON string
probe_request_instance = ProbeRequest.from_json(json)
# print the JSON string representation of the object
print(ProbeRequest.to_json())

# convert the object into a dict
probe_request_dict = probe_request_instance.to_dict()
# create an instance of ProbeRequest from a dict
probe_request_from_dict = ProbeRequest.from_dict(probe_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


