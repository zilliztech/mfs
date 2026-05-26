# mfs_sdk.BrowseApi

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**cat**](BrowseApi.md#cat) | **GET** /v1/cat | Cat
[**ls**](BrowseApi.md#ls) | **GET** /v1/ls | Ls


# **cat**
> CatResponse cat(path, range=range, meta=meta, density=density)

Cat

### Example


```python
import mfs_sdk
from mfs_sdk.models.cat_response import CatResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 
    range = 'range_example' # str |  (optional)
    meta = False # bool |  (optional) (default to False)
    density = 'density_example' # str |  (optional)

    try:
        # Cat
        api_response = api_instance.cat(path, range=range, meta=meta, density=density)
        print("The response of BrowseApi->cat:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling BrowseApi->cat: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **str**|  | 
 **range** | **str**|  | [optional] 
 **meta** | **bool**|  | [optional] [default to False]
 **density** | **str**|  | [optional] 

### Return type

[**CatResponse**](CatResponse.md)

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

# **ls**
> LsResponse ls(path)

Ls

### Example


```python
import mfs_sdk
from mfs_sdk.models.ls_response import LsResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 

    try:
        # Ls
        api_response = api_instance.ls(path)
        print("The response of BrowseApi->ls:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling BrowseApi->ls: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **str**|  | 

### Return type

[**LsResponse**](LsResponse.md)

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

