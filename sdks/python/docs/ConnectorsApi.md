# mfs_sdk.ConnectorsApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**estimate_connector**](ConnectorsApi.md#estimate_connector) | **POST** /v1/connectors/estimate | Estimate
[**inspect_connector**](ConnectorsApi.md#inspect_connector) | **GET** /v1/connectors/inspect | Inspect
[**probe_connector**](ConnectorsApi.md#probe_connector) | **POST** /v1/connectors/probe | Probe
[**remove_connector**](ConnectorsApi.md#remove_connector) | **DELETE** /v1/connectors | Remove


# **estimate_connector**
> EstimateResponse estimate_connector(probe_request)

Estimate

Zero-billing pre-flight estimate: object/chunk/token counts via
metadata + a local chunker/tokenizer dry-run. No embedding API calls.

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.estimate_response import EstimateResponse
from mfs_sdk.models.probe_request import ProbeRequest
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
    api_instance = mfs_sdk.ConnectorsApi(api_client)
    probe_request = mfs_sdk.ProbeRequest() # ProbeRequest | 

    try:
        # Estimate
        api_response = api_instance.estimate_connector(probe_request)
        print("The response of ConnectorsApi->estimate_connector:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ConnectorsApi->estimate_connector: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **probe_request** | [**ProbeRequest**](ProbeRequest.md)|  | 

### Return type

[**EstimateResponse**](EstimateResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: application/json
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

# **inspect_connector**
> object inspect_connector(target)

Inspect

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
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
    api_instance = mfs_sdk.ConnectorsApi(api_client)
    target = 'target_example' # str | 

    try:
        # Inspect
        api_response = api_instance.inspect_connector(target)
        print("The response of ConnectorsApi->inspect_connector:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ConnectorsApi->inspect_connector: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **target** | **str**|  | 

### Return type

**object**

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

# **probe_connector**
> ProbeResponse probe_connector(probe_request)

Probe

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.probe_request import ProbeRequest
from mfs_sdk.models.probe_response import ProbeResponse
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
    api_instance = mfs_sdk.ConnectorsApi(api_client)
    probe_request = mfs_sdk.ProbeRequest() # ProbeRequest | 

    try:
        # Probe
        api_response = api_instance.probe_connector(probe_request)
        print("The response of ConnectorsApi->probe_connector:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ConnectorsApi->probe_connector: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **probe_request** | [**ProbeRequest**](ProbeRequest.md)|  | 

### Return type

[**ProbeResponse**](ProbeResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

 - **Content-Type**: application/json
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

# **remove_connector**
> RemoveResponse remove_connector(target)

Remove

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.remove_response import RemoveResponse
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
    api_instance = mfs_sdk.ConnectorsApi(api_client)
    target = 'target_example' # str | 

    try:
        # Remove
        api_response = api_instance.remove_connector(target)
        print("The response of ConnectorsApi->remove_connector:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ConnectorsApi->remove_connector: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **target** | **str**|  | 

### Return type

[**RemoveResponse**](RemoveResponse.md)

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

