# LsEntry


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**name** | **str** |  | 
**type** | **str** | file | dir | 
**media_type** | **str** |  | [optional] 
**size_hint** | **int** |  | [optional] 

## Example

```python
from mfs_sdk.models.ls_entry import LsEntry

# TODO update the JSON string below
json = "{}"
# create an instance of LsEntry from a JSON string
ls_entry_instance = LsEntry.from_json(json)
# print the JSON string representation of the object
print(LsEntry.to_json())

# convert the object into a dict
ls_entry_dict = ls_entry_instance.to_dict()
# create an instance of LsEntry from a dict
ls_entry_from_dict = LsEntry.from_dict(ls_entry_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


