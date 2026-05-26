# AddResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**job_id** | **str** | sync job id; poll GET /v1/jobs/{job_id} | 

## Example

```python
from mfs_sdk.models.add_response import AddResponse

# TODO update the JSON string below
json = "{}"
# create an instance of AddResponse from a JSON string
add_response_instance = AddResponse.from_json(json)
# print the JSON string representation of the object
print(AddResponse.to_json())

# convert the object into a dict
add_response_dict = add_response_instance.to_dict()
# create an instance of AddResponse from a dict
add_response_from_dict = AddResponse.from_dict(add_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


