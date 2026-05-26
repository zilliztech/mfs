# AddRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**target** | **str** | path or connector URI to register + index | 
**full** | **bool** | force full re-index (ignore caches/fingerprints) | [optional] [default to False]
**since** | **str** | only index changes since this cursor/date | [optional] 
**process** | **bool** | True: index inline now; False: enqueue for a worker | [optional] [default to True]

## Example

```python
from mfs_sdk.models.add_request import AddRequest

# TODO update the JSON string below
json = "{}"
# create an instance of AddRequest from a JSON string
add_request_instance = AddRequest.from_json(json)
# print the JSON string representation of the object
print(AddRequest.to_json())

# convert the object into a dict
add_request_dict = add_request_instance.to_dict()
# create an instance of AddRequest from a dict
add_request_from_dict = AddRequest.from_dict(add_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


