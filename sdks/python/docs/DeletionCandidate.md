# DeletionCandidate


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**path** | **str** |  | 
**size** | **int** |  | [optional] 
**inode** | **int** |  | [optional] 
**sha1** | **str** |  | [optional] 

## Example

```python
from mfs_sdk.models.deletion_candidate import DeletionCandidate

# TODO update the JSON string below
json = "{}"
# create an instance of DeletionCandidate from a JSON string
deletion_candidate_instance = DeletionCandidate.from_json(json)
# print the JSON string representation of the object
print(DeletionCandidate.to_json())

# convert the object into a dict
deletion_candidate_dict = deletion_candidate_instance.to_dict()
# create an instance of DeletionCandidate from a dict
deletion_candidate_from_dict = DeletionCandidate.from_dict(deletion_candidate_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


