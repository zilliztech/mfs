# LsResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**entries** | [**List[LsEntry]**](LsEntry.md) |  | 

## Example

```python
from mfs_sdk.models.ls_response import LsResponse

# TODO update the JSON string below
json = "{}"
# create an instance of LsResponse from a JSON string
ls_response_instance = LsResponse.from_json(json)
# print the JSON string representation of the object
print(LsResponse.to_json())

# convert the object into a dict
ls_response_dict = ls_response_instance.to_dict()
# create an instance of LsResponse from a dict
ls_response_from_dict = LsResponse.from_dict(ls_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


