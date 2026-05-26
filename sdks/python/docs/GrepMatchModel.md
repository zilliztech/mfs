# GrepMatchModel


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**source** | **str** |  | [optional] 
**lines** | **List[int]** |  | [optional] 
**content** | **str** |  | [optional] [default to '']
**via** | **str** | bm25 | linear | pushdown | [optional] 

## Example

```python
from mfs_sdk.models.grep_match_model import GrepMatchModel

# TODO update the JSON string below
json = "{}"
# create an instance of GrepMatchModel from a JSON string
grep_match_model_instance = GrepMatchModel.from_json(json)
# print the JSON string representation of the object
print(GrepMatchModel.to_json())

# convert the object into a dict
grep_match_model_dict = grep_match_model_instance.to_dict()
# create an instance of GrepMatchModel from a dict
grep_match_model_from_dict = GrepMatchModel.from_dict(grep_match_model_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


