# mfs_sdk.RetrievalApi

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**grep**](RetrievalApi.md#grep) | **GET** /v1/grep | Grep
[**search**](RetrievalApi.md#search) | **GET** /v1/search | Search


# **grep**
> GrepResponse grep(pattern, path)

Grep

### Example


```python
import mfs_sdk
from mfs_sdk.models.grep_response import GrepResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
)


# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.RetrievalApi(api_client)
    pattern = 'pattern_example' # str | 
    path = 'path_example' # str | 

    try:
        # Grep
        api_response = api_instance.grep(pattern, path)
        print("The response of RetrievalApi->grep:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RetrievalApi->grep: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **pattern** | **str**|  | 
 **path** | **str**|  | 

### Return type

[**GrepResponse**](GrepResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **search**
> SearchResponse search(q, path=path, mode=mode, top_k=top_k, collapse=collapse)

Search

### Example


```python
import mfs_sdk
from mfs_sdk.models.search_response import SearchResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://127.0.0.1:8765
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://127.0.0.1:8765"
)


# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.RetrievalApi(api_client)
    q = 'q_example' # str | 
    path = 'path_example' # str |  (optional)
    mode = 'hybrid' # str |  (optional) (default to 'hybrid')
    top_k = 10 # int |  (optional) (default to 10)
    collapse = False # bool |  (optional) (default to False)

    try:
        # Search
        api_response = api_instance.search(q, path=path, mode=mode, top_k=top_k, collapse=collapse)
        print("The response of RetrievalApi->search:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RetrievalApi->search: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **q** | **str**|  | 
 **path** | **str**|  | [optional] 
 **mode** | **str**|  | [optional] [default to &#39;hybrid&#39;]
 **top_k** | **int**|  | [optional] [default to 10]
 **collapse** | **bool**|  | [optional] [default to False]

### Return type

[**SearchResponse**](SearchResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

