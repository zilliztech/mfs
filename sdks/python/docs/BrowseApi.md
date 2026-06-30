# mfs_sdk.BrowseApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**cat**](BrowseApi.md#cat) | **GET** /v1/cat | Cat
[**export**](BrowseApi.md#export) | **GET** /v1/export | Export
[**head**](BrowseApi.md#head) | **GET** /v1/head | Head
[**ls**](BrowseApi.md#ls) | **GET** /v1/ls | Ls
[**tail**](BrowseApi.md#tail) | **GET** /v1/tail | Tail


# **cat**
> CatResponse cat(path, range=range, meta=meta, density=density, locator=locator)

Cat

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.cat_response import CatResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 
    range = 'range_example' # str |  (optional)
    meta = False # bool |  (optional) (default to False)
    density = 'density_example' # str |  (optional)
    locator = 'locator_example' # str |  (optional)

    try:
        # Cat
        api_response = api_instance.cat(path, range=range, meta=meta, density=density, locator=locator)
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
 **locator** | **str**|  | [optional] 

### Return type

[**CatResponse**](CatResponse.md)

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

# **export**
> CatResponse export(path)

Export

Full object content for `mfs export`. Honest about completeness:
each connector's own row cap still applies (postgres `max_read_rows`,
BigQuery `max_read_rows`, etc.), so structured objects above that
threshold return `partial=true`. The bare-cat size guard
(object_too_large_for_cat) does NOT apply — export is the escape
hatch for that — but true streaming export is still TODO.

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.cat_response import CatResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 

    try:
        # Export
        api_response = api_instance.export(path)
        print("The response of BrowseApi->export:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling BrowseApi->export: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **str**|  | 

### Return type

[**CatResponse**](CatResponse.md)

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

# **head**
> CatResponse head(path, n=n)

Head

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.cat_response import CatResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 
    n = 20 # int |  (optional) (default to 20)

    try:
        # Head
        api_response = api_instance.head(path, n=n)
        print("The response of BrowseApi->head:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling BrowseApi->head: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **str**|  | 
 **n** | **int**|  | [optional] [default to 20]

### Return type

[**CatResponse**](CatResponse.md)

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

# **ls**
> LsResponse ls(path)

Ls

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.ls_response import LsResponse
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

# **tail**
> CatResponse tail(path, n=n)

Tail

### Example

* Bearer (opaque) Authentication (BearerAuth):

```python
import mfs_sdk
from mfs_sdk.models.cat_response import CatResponse
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
    api_instance = mfs_sdk.BrowseApi(api_client)
    path = 'path_example' # str | 
    n = 20 # int |  (optional) (default to 20)

    try:
        # Tail
        api_response = api_instance.tail(path, n=n)
        print("The response of BrowseApi->tail:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling BrowseApi->tail: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **path** | **str**|  | 
 **n** | **int**|  | [optional] [default to 20]

### Return type

[**CatResponse**](CatResponse.md)

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

