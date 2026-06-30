# mfs_sdk.ServerApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_server_info**](ServerApi.md#get_server_info) | **GET** /v1/server/info | Server Info
[**healthz_healthz_get**](ServerApi.md#healthz_healthz_get) | **GET** /healthz | Healthz
[**status**](ServerApi.md#status) | **GET** /v1/status | Status


# **get_server_info**
> ServerInfo get_server_info()

Server Info

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.server_info import ServerInfo
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

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**422** | Validation Error |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **healthz_healthz_get**
> Dict[str, object] healthz_healthz_get()

Healthz

Unauthenticated liveness/readiness probe (no sensitive data); used by the
compose healthcheck and Helm probes so they work even with auth enabled.

### Example


```python
import mfs_sdk
from mfs_sdk.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = mfs_sdk.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with mfs_sdk.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = mfs_sdk.ServerApi(api_client)

    try:
        # Healthz
        api_response = api_instance.healthz_healthz_get()
        print("The response of ServerApi->healthz_healthz_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ServerApi->healthz_healthz_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**Dict[str, object]**

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

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.status_response import StatusResponse
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

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**400** | Bad Request |  -  |
**401** | Unauthorized |  -  |
**404** | Not Found |  -  |
**405** | Method Not Allowed |  -  |
**422** | Validation Error |  -  |
**500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

