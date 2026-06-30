# ManifestResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**connector_uri** | **str** |  | 
**staging** | **str** |  | 
**need_sha1** | **List[str]** | paths whose bytes the server needs | [optional] 
**deletion_candidates** | [**List[DeletionCandidate]**](DeletionCandidate.md) | server-known paths absent from the manifest (for rename pairing) | [optional] 

## Example

```python
from mfs_sdk.models.manifest_response import ManifestResponse

# TODO update the JSON string below
json = "{}"
# create an instance of ManifestResponse from a JSON string
manifest_response_instance = ManifestResponse.from_json(json)
# print the JSON string representation of the object
print(ManifestResponse.to_json())

# convert the object into a dict
manifest_response_dict = manifest_response_instance.to_dict()
# create an instance of ManifestResponse from a dict
manifest_response_from_dict = ManifestResponse.from_dict(manifest_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


