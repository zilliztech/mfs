# GrepResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**results** | [**List[GrepMatchModel]**](GrepMatchModel.md) |  | 

## Example

```python
from mfs_sdk.models.grep_response import GrepResponse

# TODO update the JSON string below
json = "{}"
# create an instance of GrepResponse from a JSON string
grep_response_instance = GrepResponse.from_json(json)
# print the JSON string representation of the object
print(GrepResponse.to_json())

# convert the object into a dict
grep_response_dict = grep_response_instance.to_dict()
# create an instance of GrepResponse from a dict
grep_response_from_dict = GrepResponse.from_dict(grep_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


