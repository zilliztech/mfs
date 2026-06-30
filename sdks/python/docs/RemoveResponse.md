# RemoveResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**target** | **str** |  | 
**removed** | **bool** |  | 

## Example

```python
from mfs_sdk.models.remove_response import RemoveResponse

# TODO update the JSON string below
json = "{}"
# create an instance of RemoveResponse from a JSON string
remove_response_instance = RemoveResponse.from_json(json)
# print the JSON string representation of the object
print(RemoveResponse.to_json())

# convert the object into a dict
remove_response_dict = remove_response_instance.to_dict()
# create an instance of RemoveResponse from a dict
remove_response_from_dict = RemoveResponse.from_dict(remove_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


