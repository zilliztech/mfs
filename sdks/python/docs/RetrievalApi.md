# mfs_sdk.RetrievalApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**grep**](RetrievalApi.md#grep) | **GET** /v1/grep | Grep
[**search**](RetrievalApi.md#search) | **GET** /v1/search | Search


# **grep**
> GrepResponse grep(pattern, path)

Grep

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.grep_response import GrepResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
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

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **search**
> SearchResponse search(q, path=path, mode=mode, top_k=top_k, collapse=collapse, kind=kind)

Search

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.search_response import SearchResponse
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization (opaque): BearerAuth
configuration = mfs_sdk.Configuration(
    access_token = os.environ["BEARER_TOKEN"]
)

# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.RetrievalApi(api_client)
    q = 'q_example' # str | 
    path = 'path_example' # str |  (optional)
    mode = hybrid # str |  (optional) (default to hybrid)
    top_k = 10 # int |  (optional) (default to 10)
    collapse = False # bool |  (optional) (default to False)
    kind = 'kind_example' # str |  (optional)

    try:
        # Search
        api_response = api_instance.search(q, path=path, mode=mode, top_k=top_k, collapse=collapse, kind=kind)
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
 **mode** | **str**|  | [optional] [default to hybrid]
 **top_k** | **int**|  | [optional] [default to 10]
 **collapse** | **bool**|  | [optional] [default to False]
 **kind** | **str**|  | [optional] 

### Return type

[**SearchResponse**](SearchResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

