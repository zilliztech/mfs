# EstimateResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**target** | **str** |  | 
**type** | **str** |  | 
**objects** | **int** | total objects discovered (metadata-only count) | 
**sampled_objects** | **int** | objects actually sampled for the dry-run | 
**est_chunks** | **int** | extrapolated chunk count (±50%) | 
**est_tokens** | **int** | extrapolated token count; apply your provider rate for $ | 

## Example

```python
from mfs_sdk.models.estimate_response import EstimateResponse

# TODO update the JSON string below
json = "{}"
# create an instance of EstimateResponse from a JSON string
estimate_response_instance = EstimateResponse.from_json(json)
# print the JSON string representation of the object
print(EstimateResponse.to_json())

# convert the object into a dict
estimate_response_dict = estimate_response_instance.to_dict()
# create an instance of EstimateResponse from a dict
estimate_response_from_dict = EstimateResponse.from_dict(estimate_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


