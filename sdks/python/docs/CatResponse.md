# CatResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**source** | **str** |  | 
**content** | **str** |  | 

## Example

```python
from mfs_sdk.models.cat_response import CatResponse

# TODO update the JSON string below
json = "{}"
# create an instance of CatResponse from a JSON string
cat_response_instance = CatResponse.from_json(json)
# print the JSON string representation of the object
print(CatResponse.to_json())

# convert the object into a dict
cat_response_dict = cat_response_instance.to_dict()
# create an instance of CatResponse from a dict
cat_response_from_dict = CatResponse.from_dict(cat_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


