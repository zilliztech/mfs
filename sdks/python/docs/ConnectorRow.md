# ConnectorRow


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**root_uri** | **str** |  | 
**type** | **str** |  | 
**status** | **str** |  | 
**object_count** | **int** | indexed objects for this connector | [optional] [default to 0]
**chunk_count** | **int** | indexed vector chunks for this connector | [optional] [default to 0]

## Example

```python
from mfs_sdk.models.connector_row import ConnectorRow

# TODO update the JSON string below
json = "{}"
# create an instance of ConnectorRow from a JSON string
connector_row_instance = ConnectorRow.from_json(json)
# print the JSON string representation of the object
print(ConnectorRow.to_json())

# convert the object into a dict
connector_row_dict = connector_row_instance.to_dict()
# create an instance of ConnectorRow from a dict
connector_row_from_dict = ConnectorRow.from_dict(connector_row_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


