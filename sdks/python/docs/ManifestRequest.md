# ManifestRequest


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**client_id** | **str** | stable client id (from client.toml) — connector identity | 
**root** | **str** | client absolute path of the upload root (identity + scope) | 
**files** | [**List[ManifestFile]**](ManifestFile.md) | stat-only manifest | [optional] 

## Example

```python
from mfs_sdk.models.manifest_request import ManifestRequest

# TODO update the JSON string below
json = "{}"
# create an instance of ManifestRequest from a JSON string
manifest_request_instance = ManifestRequest.from_json(json)
# print the JSON string representation of the object
print(ManifestRequest.to_json())

# convert the object into a dict
manifest_request_dict = manifest_request_instance.to_dict()
# create an instance of ManifestRequest from a dict
manifest_request_from_dict = ManifestRequest.from_dict(manifest_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


