# mfs_sdk.ServerApi

All URIs are relative to *http://127.0.0.1:8765*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_server_info**](ServerApi.md#get_server_info) | **GET** /v1/server/info | Server Info
[**status**](ServerApi.md#status) | **GET** /v1/status | Status


# **get_server_info**
> ServerInfo get_server_info()

Server Info

### Example


```python
import mfs_sdk
from mfs_sdk.models.server_info import ServerInfo
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
    api_instance = mfs_sdk.ServerApi(api_client)

    try:
        # Server Info
        api_response = api_instance.get_server_info()
        print("The response of ServerApi->get_server_info:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ServerApi->get_server_info: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**ServerInfo**](ServerInfo.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **status**
> StatusResponse status()

Status

### Example


```python
import mfs_sdk
from mfs_sdk.models.status_response import StatusResponse
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
    api_instance = mfs_sdk.ServerApi(api_client)

    try:
        # Status
        api_response = api_instance.status()
        print("The response of ServerApi->status:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ServerApi->status: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**StatusResponse**](StatusResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

